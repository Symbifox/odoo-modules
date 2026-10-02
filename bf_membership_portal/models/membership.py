import logging

import psycopg2
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import email_normalize

from odoo.addons.bf_membership.wizard.import_members import _like_exact

_logger = logging.getLogger(__name__)

#: Demandes anonymes permises par adresse IP et par heure. Le champ piège
#: arrête les robots naïfs ; ce plafond borne les autres : chaque demande crée
#: un contact et envoie un accusé de réception, et chaque clic « Payer en
#: ligne » laisse une facture validée.
MAX_PUBLIC_PER_HOUR = 5
#: L'adresse IP ne sert qu'à ce plafond : elle s'efface au bout de ce délai.
PUBLIC_IP_RETENTION_DAYS = 7
#: Une facture de demande publique encore impayée après ce délai est annulée,
#: et la demande refusée : un robot ne laisse pas de factures orphelines.
PUBLIC_INVOICE_EXPIRY_DAYS = 14
#: Les champs que seul le formulaire public pose, en superutilisateur : jamais
#: par une valeur ni par un `default_*` du contexte d'une autre personne.
PUBLIC_FIELDS = ("public_partner_id", "public_request", "to_reconcile", "public_ip", "public_invoice_id")
#: Les champs qui ne changent plus ensuite, hors superutilisateur.
PUBLIC_FROZEN_FIELDS = ("public_partner_id", "public_request", "public_ip", "public_invoice_id")
#: Les états d'une transaction de paiement en ligne qui n'a pas abouti, mais
#: est en cours : la personne peut être sur la page du prestataire.
PAYMENT_IN_PROGRESS = ("draft", "pending", "authorized")


class Membership(models.Model):
    _inherit = "bf.membership"

    # Les champs « de bureau » du socle (notes, motifs, trace d'import) sont
    # réservés aux agents dans le socle même : le portail n'en lit aucun.

    # Réservé aux agents : le membre lit son adhésion par RPC, et n'a pas à
    # lire qu'elle vient du formulaire public (le code la lit en superutilisateur).
    public_request = fields.Boolean(
        string="Formulaire public", readonly=True, copy=False,
        groups="bf_membership.group_membership_user",
        help="La demande vient du formulaire public d'adhésion.",
    )
    # 🔴 `readonly` ne vaut que pour la vue : `write()` et `create()` refusent
    # ce champ hors du code du module. `_public_payable` s'y fie.
    public_partner_id = fields.Many2one(
        "res.partner", string="Contact créé par le formulaire", readonly=True, copy=False,
        index="btree_not_null", ondelete="set null",
        groups="bf_membership.group_membership_user",
        help="Le contact que le formulaire public a créé avec ce qu'on y a tapé. Seule une "
             "demande encore portée par ce contact se paie en ligne depuis le formulaire.",
    )
    # La facture que le clic « Payer en ligne » a créée : seule celle-là est
    # l'affaire du ménage quotidien, du refus et du rattachement. Une facture
    # émise par l'équipe sur une demande publique reste à l'équipe.
    public_invoice_id = fields.Many2one(
        "account.move", string="Facture du paiement en ligne", readonly=True, copy=False,
        ondelete="set null", groups="bf_membership.group_membership_user",
    )
    to_reconcile = fields.Boolean(
        string="À rapprocher", copy=False, tracking=True,
        groups="bf_membership.group_membership_user",
        help="Un contact avait déjà ce courriel quand la demande est arrivée. Le "
             "formulaire public ne touche jamais un contact existant : il en crée "
             "un neuf, et une personne vérifie, puis rattache la demande au contact "
             "existant au besoin.",
    )
    public_ip = fields.Char(
        string="Envoyée depuis", readonly=True, copy=False,
        groups="bf_membership.group_membership_manager",
        help="L'adresse IP de la demande publique, pour freiner les envois en rafale. "
             "Effacée au bout de quelques jours.",
    )

    # ------------------------------------------------------------------
    # Garde
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        """🔴 Hors superutilisateur, aucun champ du formulaire public : ni par
        les valeurs, ni par un `default_*` du contexte, que l'ORM applique APRÈS
        les contrôles et qu'un appel direct choisit librement. Sans cela, un
        agent forgerait une « demande publique » sur le contact de son choix,
        liée à la facture de son choix, que le refus, le ménage quotidien ou le
        rattachement traiteraient ensuite en superutilisateur."""
        if not self.env.su:
            self = self.with_context({k: v for k, v in self.env.context.items()
                                      if not (k.startswith("default_") and k[len("default_"):] in PUBLIC_FIELDS)})
            forged = {k for vals in vals_list for k, v in vals.items() if k in PUBLIC_FIELDS and v}
            if forged:
                raise AccessError(_(
                    "Seul le formulaire public pose ces champs : %s.", ", ".join(sorted(forged))))
        records = super().create(vals_list)
        if not self.env.su and any(rec[f] for rec in records.sudo() for f in PUBLIC_FIELDS):
            raise AccessError(_("Seul le formulaire public crée une demande publique."))
        return records

    def write(self, vals):
        if not self.env.su:
            for field in set(PUBLIC_FROZEN_FIELDS) & vals.keys():
                value = vals[field] or False
                for rec in self.sudo():
                    current = rec[field].id if field == "public_partner_id" else rec[field]
                    if (current or False) != value:
                        raise AccessError(_("Ce que le formulaire public a noté ne se change pas."))
        return super().write(vals)

    def action_mark_reconciled(self):
        self.write({"to_reconcile": False})

    # ------------------------------------------------------------------
    # Le formulaire public
    # ------------------------------------------------------------------

    @api.model
    def _public_requests_exceeded(self, ip):
        if not ip:
            return False
        since = fields.Datetime.subtract(fields.Datetime.now(), hours=1)
        return self.sudo().search_count([
            ("public_ip", "=", ip), ("create_date", ">=", since)]) >= MAX_PUBLIC_PER_HOUR

    @api.model
    def _create_public_request(self, mtype, values, ip=None):
        """Une demande d'adhésion venue du formulaire public. Rend l'adhésion.

        🔴 Le contact est TOUJOURS neuf. N'importe qui peut taper l'adresse de
        n'importe qui dans un formulaire public : rattacher la demande au
        contact qui porte ce courriel donnerait à un inconnu le pouvoir de
        changer l'adresse, les consentements ou la catégorie d'un membre, et de
        se voir répondre « vous êtes déjà membre » (Loi 25 : le formulaire
        révélerait qui est au registre). La demande est marquée « à rapprocher »
        quand le courriel existe déjà ; une personne vérifie et la rattache au
        contact existant (`_attach_to_partner`).

        🔴 Aucune facture ici. Une facture validée ne se supprime pas : en
        émettre une à chaque envoi du formulaire laisserait une facture
        orpheline par robot ou par curieux. La facture naît seulement quand la
        personne clique « Payer en ligne » (`_public_pay_invoice`). Une
        catégorie sur décision attend la décision.

        Appelé en superutilisateur par le contrôleur, après le contrôle du
        jeton CSRF, du champ piège et du plafond par adresse IP.
        """
        email = email_normalize(values["email"])
        known = bool(self.env["res.partner"].with_context(active_test=False).search_count(
            [("email", "=ilike", _like_exact(email))]))
        is_company = mtype.member_kind == "organization" or (
            mtype.member_kind == "both" and bool(values.get("is_company")))
        company = mtype.company_id
        partner = self.env["res.partner"].with_context(mail_create_nosubscribe=True).create({
            "name": values["name"],
            "is_company": is_company,
            "email": email,
            "phone": values.get("phone") or False,
            "street": values.get("street") or False,
            "city": values.get("city") or False,
            "zip": values.get("zip") or False,
            "country_id": company.country_id.id or False,
            "lang": values.get("lang") or False,
            "directory_consent": bool(values.get("directory_consent")),
            "notice_email_consent": bool(values.get("notice_email_consent")),
        })
        membership = self.with_company(company).with_context(mail_create_nosubscribe=True).create({
            "partner_id": partner.id,
            "public_partner_id": partner.id,
            "type_id": mtype.id,
            "company_id": company.id,
            "public_request": True,
            "to_reconcile": known,
            "public_ip": ip or False,
        })
        membership._log_public_consents()
        membership._notify_public_request(known)
        return membership

    def _log_public_consents(self):
        """La preuve des consentements donnés au formulaire public (Loi 25 : qui,
        quand, par où), sur la demande, que seuls les agents lisent.

        🔴 Le socle ne consigne un consentement posé à la création d'un contact
        que si une adhésion existe déjà : le formulaire crée le contact avant
        la demande. Les deux consentements sont notés, cochés ou non : un refus
        se prouve aussi. Qui : la personne qui a envoyé le formulaire, sous le
        nom et l'adresse qu'elle a tapés ; quand : la date du message.
        """
        self.ensure_one()
        partner = self.sudo().partner_id
        yes, no = _("oui"), _("non")
        self._message_log(body=_(
            "Consentements donnés au formulaire public par la personne qui a envoyé la demande "
            "(%(name)s, %(email)s) : %(directory_label)s : %(directory)s ; %(notice_label)s : %(notice)s.",
            name=partner.name, email=partner.email or "",
            directory_label=partner._fields["directory_consent"].string,
            directory=yes if partner.directory_consent else no,
            notice_label=partner._fields["notice_email_consent"].string,
            notice=yes if partner.notice_email_consent else no))

    def _public_request_managers(self):
        """Les responsables des membres de la société, à prévenir d'une demande."""
        self.ensure_one()
        group = self.env.ref("bf_membership.group_membership_manager")
        return group.users.filtered(
            lambda u: u.active and not u.share and not u._is_superuser()
            and self.company_id in u.company_ids)

    def _notify_public_request(self, known):
        """Prévenir l'équipe et accuser réception.

        * Les responsables des membres reçoivent une notification Odoo, en
          boîte de réception ou par courriel selon leur préférence : sans
          elle, une demande attend que quelqu'un pense à ouvrir « Demandes ».
        * La personne reçoit un accusé de réception sobre, à l'adresse saisie.
          🔴 Il est le même que le courriel soit connu ou non : la mention « à
          rapprocher » ne va qu'à l'équipe. Il ne reprend AUCUN texte saisi au
          formulaire, pas même le nom, ni dans le corps ni dans l'en-tête « À »
          (il part à l'adresse seule, par `email_to`) : sinon, n'importe qui
          ferait partir de l'adresse de l'organisme un texte de son choix vers
          une adresse de son choix. Le plafond par adresse IP et le champ piège
          bornent le reste.
        """
        self.ensure_one()
        body = Markup("<p>%s</p>") % _(
            "Nouvelle demande d'adhésion reçue du formulaire public : %(name)s (%(type)s).",
            name=self.partner_id.name, type=self.type_id.name)
        if known:
            body += Markup("<p>%s</p>") % _(
                "Un contact existant a le même courriel : à rapprocher avant toute décision. "
                "Le formulaire n'a modifié aucun contact existant.")
        # 🔴 Une note interne, et un objet générique. En commentaire, le membre
        # à qui la demande serait ensuite rattachée lirait au portail ce que
        # l'inconnu a tapé et la mention « à rapprocher » ; dans l'objet, le
        # nom tapé (texte libre d'un inconnu) partirait par courriel. Les
        # responsables restent avisés par `partner_ids`.
        self.message_post(
            body=body, subtype_xmlid="mail.mt_note", message_type="comment",
            subject=_("Nouvelle demande d'adhésion"),
            author_id=self.company_id.partner_id.id,
            partner_ids=self._public_request_managers().partner_id.ids)
        template = self.env.ref("bf_membership_portal.mail_template_signup_ack", raise_if_not_found=False)
        if template and self.partner_id.email:
            template.send_mail(self.id)

    def _public_payable(self):
        """Une demande publique admise d'office, à payer, avec un montant, et
        encore portée par le contact que le formulaire a créé.

        🔴 Ce dernier point ferme la fuite d'une fusion : une fois la demande
        rattachée au contact existant (ou ce contact fusionné), la facture
        partirait au nom et à l'adresse de la vraie personne, et la session qui
        a envoyé le formulaire la lirait par son lien à jeton.

        🔴 « À rapprocher » n'entre PAS ici : la page et le bouton restent les
        mêmes que le courriel soit connu ou non, sinon la différence dirait qui
        est au registre.
        """
        self.ensure_one()
        return (self.public_request and self.state == "waiting" and self.payment_state == "to_pay"
                and bool(self.public_partner_id) and self.partner_id == self.public_partner_id
                and self.currency_id.compare_amounts(self.amount, 0.0) > 0)

    def _public_pay_invoice(self):
        """La facture à payer en ligne, créée au premier clic « Payer en ligne ».

        🔴 Deux clics, une facture. La ligne de l'adhésion est verrouillée avant
        de relire `invoice_id` : deux clics simultanés ne voient pas tous deux
        « pas encore de facture ». Les clics suivants retrouvent la facture du
        premier.
        """
        self.ensure_one()
        self.env.cr.execute("SELECT id FROM bf_membership WHERE id = %s FOR UPDATE", [self.id])
        self.invalidate_recordset(["invoice_id"])
        invoice = self.invoice_id
        if invoice and invoice.state == "posted":
            return invoice
        invoice = self._create_invoice(salesperson=self.env["res.users"])
        self.sudo().public_invoice_id = invoice
        return invoice

    # ------------------------------------------------------------------
    # Rattacher une demande au contact existant
    # ------------------------------------------------------------------

    def action_attach_existing(self):
        self.ensure_one()
        self.check_access("write")
        return {
            "type": "ir.actions.act_window",
            "name": _("Rattacher au contact existant"),
            "res_model": "bf.membership.attach",
            "view_mode": "form",
            "target": "new",
            "context": {"default_membership_id": self.id},
        }

    @staticmethod
    def _online_payment_pending(move):
        """Un paiement en ligne est en cours sur la facture : il ne faut pas
        l'annuler sous les pieds de la personne qui paie."""
        return bool(move.sudo().transaction_ids.filtered(lambda t: t.state in PAYMENT_IN_PROGRESS))

    def _cancel_unpaid_invoice(self, detach):
        """Annuler la facture impayée de l'adhésion (remise en brouillon, puis
        annulée, en superutilisateur) et, si `detach`, l'en séparer. Rend la
        facture annulée, ou rien si la facture a reçu un paiement, même
        partiel, ou si un paiement en ligne est en cours : elle reste alors
        telle quelle."""
        self.ensure_one()
        rec = self.sudo()
        move = rec.invoice_id
        if (not move or move.state == "cancel" or move.payment_state != "not_paid"
                or self._online_payment_pending(move)):
            return self.env["account.move"]
        # 🔴 En défense : seulement la facture de CETTE demande publique, au
        # contact du formulaire et pour l'article de la catégorie. Ce geste se
        # fait en superutilisateur ; il n'annule jamais une autre facture.
        form = rec.public_partner_id
        product = rec.type_id.product_id
        if (not rec.public_request or not form or move != rec.public_invoice_id
                or move.commercial_partner_id != form.commercial_partner_id
                or not product or product not in move.invoice_line_ids.product_id):
            return self.env["account.move"]
        if move.state == "posted":
            move.button_draft()
        move.button_cancel()
        # 🔴 Le lien que le visiteur a reçu porte ce jeton. Annulée, la facture
        # pourrait encore changer de partenaire (une fusion réécrit les clés en
        # SQL) : un jeton neuf fait mourir l'ancien lien avec elle.
        move.access_token = False
        if detach:
            self.sudo().write({"invoice_id": False, "payment_source": False})
        return move

    def _attach_to_partner(self, target):
        """Rattacher une demande du formulaire public au contact existant.

        🔴 C'est le chemin à la place de la fusion des contacts : l'assistant
        de fusion d'Odoo réécrit en SQL tout ce qui pointe le contact du
        formulaire, factures comprises, et une facture émise au nom tapé au
        formulaire passerait au nom et à l'adresse de la vraie personne.

        * l'adhésion passe au contact choisi, et n'est plus « à rapprocher » ;
        * la facture du formulaire, si elle est impayée, est annulée et séparée
          de l'adhésion ; payée (même en partie), ou pendant un paiement en
          ligne en cours, elle reste liée ;
        * le contact du formulaire, qui n'a plus d'adhésion, est supprimé ;
          si quelque chose le pointe encore (une facture, même annulée, un
          paiement), il est archivé. Il ne reste pas actif avec le courriel de
          la vraie personne ;
        * le numéro de membre : le contact existant garde le sien ; s'il n'en a
          pas, il reprend celui du contact du formulaire, sinon un neuf vient
          à la mise en règle.
        """
        self.ensure_one()
        self.check_access("write")
        rec = self.sudo()
        form = rec.partner_id
        target = target.sudo()
        if not (rec.public_request and rec.public_partner_id and form == rec.public_partner_id):
            raise UserError(_(
                "Seule une demande du formulaire public, encore portée par le contact qu'il a créé, "
                "se rattache à un contact existant."))
        if not target or target == form:
            raise UserError(_("Choisissez le contact existant auquel rattacher la demande."))
        cancelled = rec._cancel_unpaid_invoice(detach=True)
        rec.write({"partner_id": target.id, "to_reconcile": False})
        number = form.member_number
        if number and not target.member_number and not form.membership_ids:
            form.member_number = False
            form.flush_recordset(["member_number"])
            target.member_number = number
        rec._ensure_numbers()
        form_name = form.display_name
        outcome = rec._remove_form_partner(form)
        texts = {
            "deleted": _("Demande rattachée au contact existant %(target)s. Le contact créé par le "
                         "formulaire (%(form)s) est supprimé."),
            "archived": _("Demande rattachée au contact existant %(target)s. Le contact créé par le "
                          "formulaire (%(form)s) est archivé : une facture ou un paiement le porte encore."),
            "kept": _("Demande rattachée au contact existant %(target)s. Le contact %(form)s est gardé "
                      "tel quel : il a un accès ou d'autres adhésions."),
        }
        body = Markup("<p>%s</p>") % (texts[outcome] % {"target": target.display_name, "form": form_name})
        if cancelled:
            body += Markup("<p>%s</p>") % _(
                "La facture du formulaire %s, impayée, est annulée.", cancelled.name)
        elif rec.invoice_id:
            body += Markup("<p>%s</p>") % _(
                "La facture %s a reçu un paiement, ou un paiement en ligne est en cours : elle reste "
                "liée à l'adhésion, au nom du contact du formulaire.", rec.invoice_id.name)
        rec.message_post(body=body, subtype_xmlid="mail.mt_note")
        return True

    def _remove_form_partner(self, form):
        """Supprimer le contact du formulaire, ou l'archiver si quelque chose le
        pointe encore. Rend « deleted », « archived » ou « kept ».

        🔴 En défense, ce geste fait en superutilisateur ne touche que le
        contact du formulaire de CETTE demande publique, et jamais un contact
        qui a un accès (usager) ou qui porte encore une adhésion.
        """
        self.ensure_one()
        rec = self.sudo()
        form = form.sudo()
        if (not rec.public_request or form != rec.public_partner_id or form.user_ids
                or form.with_context(active_test=False).membership_ids):
            return "kept"
        try:
            with self.env.cr.savepoint():
                form.unlink()
            return "deleted"
        except (UserError, psycopg2.IntegrityError):
            self.env.invalidate_all()
            form.active = False
            return "archived"

    def action_refuse(self):
        """Une demande publique encore portée par le contact du formulaire se
        refuse même si la session a cliqué « Payer en ligne » : sa facture
        impayée est d'abord annulée et séparée de la demande, en
        superutilisateur. Sans cela, l'agent sans droits de facturation ne
        pourrait ni refuser la demande en double ni la rattacher."""
        self.check_access("write")
        for rec in self.sudo():
            if rec.public_request and rec.public_partner_id and rec.partner_id == rec.public_partner_id:
                cancelled = rec._cancel_unpaid_invoice(detach=True)
                if cancelled:
                    rec.message_post(subtype_xmlid="mail.mt_note", body=_(
                        "La facture du formulaire %s, impayée, est annulée avant le refus.", cancelled.name))
        return super().action_refuse()

    # ------------------------------------------------------------------
    # La fusion de contacts
    # ------------------------------------------------------------------

    @api.model
    def _check_partner_merge(self, partners, dst_partner):
        """Appelé par l'assistant de fusion avant de fusionner `partners` dans
        `dst_partner`.

        🔴 L'assistant d'Odoo réécrit en SQL tout ce qui pointe les contacts
        fusionnés. Un contact créé par le formulaire public qui porte une
        facture ne se fusionne pas : la facture, et ce que son lien de paiement
        montre, passeraient au nom et à l'adresse de l'autre contact. Il n'est
        jamais non plus le contact GARDÉ : ses données et ses consentements
        sont ceux qu'un inconnu a tapés. Sans facture, il se fusionne dans le
        contact existant, et la demande perd le contact du formulaire : elle
        ne se paie plus en ligne depuis le formulaire.
        """
        if len(partners) < 2:
            return
        memberships = self.sudo().search([("public_partner_id", "in", partners.ids)])
        if not memberships:
            return
        forms = memberships.public_partner_id
        if dst_partner in forms:
            raise UserError(_(
                "Le contact « %s » vient du formulaire public d'adhésion : il ne peut pas être le "
                "contact gardé d'une fusion, ses données et ses consentements sont ceux qu'un inconnu "
                "a tapés. Gardez le contact existant, ou utilisez « Rattacher au contact existant » "
                "sur la demande.", dst_partner.display_name))
        moves = self.env["account.move"].sudo().search([
            ("move_type", "in", ("out_invoice", "out_refund", "out_receipt")),
            "|", ("partner_id", "in", forms.ids), ("commercial_partner_id", "in", forms.ids),
        ])
        invoiced = (moves.partner_id | moves.commercial_partner_id) & forms
        if invoiced:
            raise UserError(_(
                "Le contact « %s » vient du formulaire public d'adhésion et porte une facture : la "
                "fusion ferait passer cette facture, et ce que son lien de paiement montre, au nom de "
                "l'autre contact. Ouvrez la demande d'adhésion et utilisez « Rattacher au contact "
                "existant ».", invoiced[:1].display_name))
        memberships.write({"public_partner_id": False})

    # ------------------------------------------------------------------
    # La passe quotidienne
    # ------------------------------------------------------------------

    @api.model
    def _cron_public_requests_daily(self):
        """Le ménage quotidien des demandes publiques."""
        self._cron_forget_public_ip()
        self._cron_expire_unpaid_public_invoices()

    @api.model
    def _cron_forget_public_ip(self):
        """Effacer l'adresse IP des demandes publiques une fois le délai passé."""
        until = fields.Datetime.subtract(fields.Datetime.now(), days=PUBLIC_IP_RETENTION_DAYS)
        self.sudo().search([("public_ip", "!=", False), ("create_date", "<", until)]).write({"public_ip": False})

    @api.model
    def _cron_expire_unpaid_public_invoices(self):
        """Une demande publique dont la facture reste impayée après
        `PUBLIC_INVOICE_EXPIRY_DAYS` jours : la facture est annulée, la demande
        refusée. N'envoie rien.

        Seulement les demandes encore portées par le contact du formulaire :
        une demande rattachée à un membre existant, puis facturée par
        l'équipe, n'est plus l'affaire de ce ménage. Une facture qui a reçu un
        paiement, même partiel, ou dont un paiement en ligne est en cours, est
        laissée à l'équipe.
        """
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=PUBLIC_INVOICE_EXPIRY_DAYS)
        candidates = self.sudo().search([
            ("public_request", "=", True),
            ("state", "=", "waiting"),
            ("payment_state", "=", "to_pay"),
            ("invoice_id.state", "=", "posted"),
            ("invoice_id.payment_state", "=", "not_paid"),
            ("invoice_id.create_date", "<", cutoff),
        ])
        for rec in candidates:
            move = rec.invoice_id
            # Seule la facture du clic « Payer en ligne » : une facture émise par
            # l'équipe sur une demande publique reste à l'équipe.
            if (rec.partner_id != rec.public_partner_id or move != rec.public_invoice_id
                    or self._online_payment_pending(move)):
                continue
            try:
                with self.env.cr.savepoint():
                    rec._cancel_unpaid_invoice(detach=False)
                    rec.action_refuse()
                    rec.message_post(subtype_xmlid="mail.mt_note", body=_(
                        "Facture %(invoice)s annulée et demande refusée : impayée après %(days)s jours.",
                        invoice=move.name, days=PUBLIC_INVOICE_EXPIRY_DAYS))
            except UserError:
                _logger.warning("Demande publique %s : facture %s non annulée", rec.id, move.name, exc_info=True)
