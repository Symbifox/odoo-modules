import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare

from odoo.addons.bf_membership.models.membership import keep_trace

_logger = logging.getLogger(__name__)

#: La clé de contexte des écritures que le module fait lui-même : la
#: synchronisation de l'adhésion depuis sa facture. 🔴 Elle ne vaut qu'en
#: superutilisateur : un contexte se forge dans un appel RPC, le mode
#: superutilisateur non.
INVOICE_SYNC_KEY = "bf_membership_invoice_sync"
#: Le nom et l'adresse que le reçu fiscal porte, figés au paiement.
DONOR_FIELDS = ("donor_name", "donor_address")
#: Les champs que seul le code du module pose : jamais par une valeur ni par un
#: `default_*` du contexte d'une création faite par une autre personne.
CODE_ONLY_FIELDS = ("invoice_id",) + DONOR_FIELDS


class Membership(models.Model):
    """L'adhésion facturée.

    🔴 Tant qu'une facture validée porte la cotisation (`payment_source` à
    « facture »), c'est elle qui dit si la cotisation est payée : le paiement
    s'enregistre sur la facture, et l'adhésion suit. Noter un paiement à la
    main sur l'adhésion pendant que la facture reste ouverte ferait deux
    vérités, et la suivante des deux à bouger effacerait l'autre sans le dire.
    Tant que la facture porte la cotisation, l'état du paiement, sa source, son
    montant et sa date ne s'écrivent pas à la main, et le membre, la catégorie
    et la société de l'adhésion ne changent plus. Pour sortir une adhésion de
    sa facture : un avoir, ou l'annulation de la facture.

    🔴 Le reçu fiscal lit le montant et la date du paiement. Ils sont figés
    dès qu'une facture validée porte la cotisation ou qu'elle est réglée, et
    la facture elle-même ne se lie que par le code du module.
    """

    _inherit = "bf.membership"

    # 🔴 `readonly` ne vaut que pour la vue : `write()` et `create()` refusent
    # ce champ hors du code du module (voir `_check_invoice_link`). Réservé aux
    # agents : le portail lit l'adhésion du membre par RPC, et le nom de la
    # facture d'une demande rattachée est celui qu'un inconnu a tapé. Le
    # portail lit la facture en superutilisateur, après son propre contrôle.
    invoice_id = fields.Many2one(
        "account.move", string="Facture", readonly=True, copy=False, index=True,
        ondelete="set null", tracking=True, groups="bf_membership.group_membership_user",
    )
    invoice_state = fields.Selection(
        related="invoice_id.state", string="État de la facture", groups="bf_membership.group_membership_user")
    invoice_payment_state = fields.Selection(
        related="invoice_id.payment_state", string="Paiement de la facture",
        groups="bf_membership.group_membership_user")
    # 🔴 Figés au paiement, en superutilisateur : le reçu fiscal les porte.
    # Relus sur le contact au moment de délivrer, ils laissaient un agent
    # renommer le contact, délivrer le reçu au nom d'un tiers, puis remettre le
    # nom, sans trace.
    donor_name = fields.Char(
        string="Nom au reçu", readonly=True, copy=False, tracking=True,
        groups="bf_membership.group_membership_user",
        help="Le nom du membre quand la cotisation a été payée. Le reçu fiscal le porte.")
    donor_address = fields.Text(
        string="Adresse au reçu", readonly=True, copy=False, tracking=True,
        groups="bf_membership.group_membership_user",
        help="L'adresse du membre quand la cotisation a été payée, si elle était complète. "
             "Le reçu fiscal la porte.")
    invoice_drives = fields.Boolean(
        string="Payée par la facture", compute="_compute_invoice_drives",
        help="Une facture validée porte la cotisation : le paiement s'enregistre sur la facture.",
    )
    receipt_eligible = fields.Boolean(related="type_id.receipt_eligible")
    receipt_eligible_amount = fields.Monetary(
        string="Montant admissible au reçu", compute="_compute_receipt_eligible_amount",
    )
    receipt_ids = fields.One2many(
        "bf.membership.receipt", "membership_id", string="Reçus fiscaux",
        groups="bf_membership.group_membership_user",
    )
    receipt_count = fields.Integer(
        compute="_compute_receipt_count", groups="bf_membership.group_membership_user")

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------

    @api.depends("invoice_id.state", "invoice_id.payment_state", "payment_source")
    def _compute_invoice_drives(self):
        for rec in self:
            rec.invoice_drives = rec._invoice_drives()

    @api.depends("amount", "type_id.receipt_eligible", "type_id.advantage_amount")
    def _compute_receipt_eligible_amount(self):
        for rec in self:
            rec.receipt_eligible_amount = (
                rec.type_id._eligible_amount(rec.amount)[0] if rec.type_id.receipt_eligible else 0.0)

    def _compute_receipt_count(self):
        for rec in self:
            rec.receipt_count = len(rec.receipt_ids)

    def _invoice_drives(self, payment_source=None):
        """La facture décide du paiement : validée, ni annulée ni renversée, et
        la source du paiement est la facture."""
        self.ensure_one()
        move = self.invoice_id.sudo()
        source = self.payment_source if payment_source is None else payment_source
        return (bool(move) and source == "invoice" and move.state == "posted"
                and not move._bf_membership_credited())

    def _invoice_carries(self):
        """Une facture validée, ni annulée ni renversée, porte la cotisation,
        quelle que soit la source de paiement notée."""
        self.ensure_one()
        move = self.invoice_id.sudo()
        return bool(move) and move.state == "posted" and not move._bf_membership_credited()

    # ------------------------------------------------------------------
    # Gardes : une seule vérité sur le paiement, et ce que le reçu lit
    # ------------------------------------------------------------------

    def _is_invoice_sync(self):
        """Vrai pour les écritures internes de synchronisation depuis la
        facture : la clé de contexte ET le mode superutilisateur."""
        return self.env.su and bool(self.env.context.get(INVOICE_SYNC_KEY))

    def _payment_frozen(self):
        """Le montant et la date du paiement ne changent plus : une facture
        validée porte la cotisation, ou la cotisation est réglée (payée ou
        exemptée). Le reçu fiscal les lit."""
        self.ensure_one()
        return self.payment_state in ("paid", "exempt") or self._invoice_carries()

    def _identity_frozen(self):
        """Le socle fige le membre, la catégorie et la société d'une adhésion
        payée, exemptée ou qui a fait quelqu'un membre. En plus : une facture
        validée la porte. Sinon le paiement de la facture d'une personne ferait
        membre une autre personne, et le reçu fiscal partirait à son nom."""
        return super()._identity_frozen() or self._invoice_carries()

    def _check_invoice_link(self, vals_list):
        """🔴 La facture se lie par le code du module (bouton « Facturer »,
        paiement en ligne), jamais par une écriture : un agent qui écrirait
        `invoice_id` par RPC lirait le nom et l'état de la facture d'un autre
        client, et la synchronisation suivrait ensuite cette facture."""
        if self.env.su:
            return
        for vals in vals_list:
            if "invoice_id" not in vals:
                continue
            target = vals["invoice_id"] or False
            if not self or any(rec.invoice_id.id != target for rec in self):
                raise AccessError(_(
                    "La facture d'une adhésion ne se lie que par le bouton « Facturer » "
                    "ou par le paiement en ligne."))

    @api.model_create_multi
    def create(self, vals_list):
        self = keep_trace(self)
        if not self.env.su:
            # 🔴 Les valeurs ne suffisent pas : l'ORM complète la création par
            # les `default_*` du contexte APRÈS ce contrôle, et un appel direct
            # choisit son contexte. Les défauts sont retirés, les valeurs
            # refusées, et le résultat vérifié.
            self = self.with_context({k: v for k, v in self.env.context.items()
                                      if not (k.startswith("default_") and k[len("default_"):] in CODE_ONLY_FIELDS)})
            self.browse()._check_invoice_link([v for v in vals_list if v.get("invoice_id")])
            if any(v.get(f) for v in vals_list for f in DONOR_FIELDS):
                raise AccessError(_("Le nom et l'adresse au reçu se figent seuls, au paiement."))
        records = super().create(vals_list)
        if not self.env.su and any(rec[f] for rec in records.sudo() for f in CODE_ONLY_FIELDS):
            raise AccessError(_(
                "La facture d'une adhésion ne se lie que par le bouton « Facturer » "
                "ou par le paiement en ligne."))
        records.filtered(lambda r: r.payment_state == "paid")._snapshot_donor()
        return records

    def _source_frozen(self):
        """La source du paiement ne change plus : une facture validée porte la
        cotisation, la cotisation est réglée, ou un reçu fiscal a été délivré.
        🔴 Le cercle de qui délivre et annule un reçu en dépendait : un agent
        passait la source à « Facture » le temps d'annuler un reçu."""
        self.ensure_one()
        return self._payment_frozen() or bool(
            self.sudo().receipt_ids.filtered(lambda r: r.state == "issued"))

    def write(self, vals):
        self = keep_trace(self)
        self._check_invoice_link([vals])
        if not self.env.su and set(DONOR_FIELDS) & vals.keys():
            raise AccessError(_("Le nom et l'adresse au reçu se figent seuls, au paiement."))
        sync = self._is_invoice_sync()
        if "payment_source" in vals and not sync:
            for rec in self:
                if rec._source_frozen() and rec.payment_source != (vals["payment_source"] or False):
                    raise UserError(_(
                        "%(name)s : la cotisation est facturée, réglée ou a reçu un reçu fiscal ; la "
                        "source de son paiement ne change plus. Une facture se renverse par un avoir ; "
                        "un paiement noté à la main se remet d'abord « à payer ».",
                        name=rec.display_name))
        if "payment_state" in vals and not sync:
            for rec in self:
                if not rec._invoice_carries():
                    continue
                expected = "paid" if rec.invoice_id.sudo()._bf_membership_settled() else "to_pay"
                if vals["payment_state"] != expected:
                    raise UserError(_(
                        "La cotisation de %(name)s est facturée (%(invoice)s) : le paiement "
                        "s'enregistre sur la facture, et l'adhésion suit. Pour exempter ce "
                        "membre ou noter un paiement reçu ailleurs, renversez d'abord la "
                        "facture par un avoir.",
                        name=rec.partner_id.name, invoice=rec.invoice_id.sudo().name))
        # 🔴 Comparé APRÈS l'écriture : changer la catégorie recalcule la
        # cotisation sans que `amount` paraisse dans `vals`. Réécrire la même
        # valeur reste permis (l'import le fait).
        frozen = self.browse() if sync else self.filtered(lambda r: r._payment_frozen())
        before = {rec.id: (rec.amount, rec.payment_date) for rec in frozen}
        was_paid = {rec.id: rec.payment_state == "paid" for rec in self} if "payment_state" in vals else None
        res = super().write(vals)
        for rec in frozen:
            amount, payment_date = before[rec.id]
            if rec.currency_id.compare_amounts(rec.amount, amount) or rec.payment_date != payment_date:
                raise UserError(_(
                    "%(name)s : la cotisation est facturée ou réglée ; son montant et la date de son "
                    "paiement ne changent plus, le reçu fiscal les porte. Une erreur se corrige par un "
                    "avoir sur la facture ; un paiement noté à la main se remet d'abord « à payer ».",
                    name=rec.display_name))
        if was_paid is not None:
            self.filtered(lambda r: r.payment_state == "paid" and not was_paid[r.id])._snapshot_donor()
            unpaid = self.filtered(lambda r: r.payment_state != "paid" and was_paid[r.id])
            if unpaid:
                unpaid.sudo().write(dict.fromkeys(DONOR_FIELDS, False))
        return res

    # ------------------------------------------------------------------
    # Le donateur, figé au paiement
    # ------------------------------------------------------------------

    def _snapshot_donor(self):
        """Figer, en superutilisateur, le nom et l'adresse du membre que le reçu
        portera. L'adresse n'est figée que complète (rue et ville). Le suivi
        des deux champs en garde la trace au fil de l'adhésion."""
        for rec in self:
            partner = rec.partner_id.sudo()
            complete = bool(partner.street and partner.city)
            rec.sudo().write({
                "donor_name": partner.name,
                "donor_address": rec._clean_address(partner._display_address(without_company=True))
                if complete else False,
            })

    @staticmethod
    def _clean_address(address):
        """L'adresse au reçu sans lignes vides ni espaces de fin : le gabarit
        d'adresse d'Odoo laisse une ligne vide pour chaque champ absent (rue 2,
        province) et des espaces derrière la ville."""
        return "\n".join(line.strip() for line in (address or "").splitlines() if line.strip()) or False

    @api.model
    def _fill_missing_donor_snapshots(self):
        """Pour la migration : figer le donateur des adhésions déjà payées qui
        n'en ont pas, depuis le contact tel qu'il est aujourd'hui."""
        self.sudo().search([("payment_state", "=", "paid"), ("donor_name", "=", False)])._snapshot_donor()

    def action_refresh_donor(self):
        """Figer de nouveau le nom et l'adresse au reçu depuis le contact d'aujourd'hui :
        une adresse complétée après le paiement, un nom corrigé. Réservé à la
        personne responsable des membres ; le suivi en garde la trace."""
        if not self.env.su and not self.env.user.has_group("bf_membership.group_membership_manager"):
            raise AccessError(_(
                "Le nom et l'adresse au reçu se mettent à jour par une personne responsable des membres."))
        self.check_access("write")
        if any(rec.payment_state != "paid" for rec in self):
            raise UserError(_("Le nom et l'adresse au reçu se figent sur une cotisation payée."))
        self._snapshot_donor()

    def _refusable(self):
        """Le socle refuse une demande, ou une adhésion acceptée jamais payée.
        En plus : aucune facture validée (ni annulée ni renversée) ne porte la
        cotisation. Refuser l'adhésion laisserait une créance ouverte."""
        return super()._refusable() and not self._invoice_carries()

    def action_refuse(self):
        for rec in self:
            if rec._invoice_carries():
                raise UserError(_(
                    "%(name)s est facturée (%(invoice)s) : annulez la facture, ou renversez-la par un "
                    "avoir, avant de refuser l'adhésion.",
                    name=rec.display_name, invoice=rec.invoice_id.sudo().name))
        return super().action_refuse()

    def unlink(self):
        for rec in self:
            if rec._invoice_carries():
                raise UserError(_(
                    "%(name)s est facturée (%(invoice)s) : annulez la facture, ou renversez-la par un "
                    "avoir, avant de supprimer l'adhésion.",
                    name=rec.display_name, invoice=rec.invoice_id.sudo().name))
        return super().unlink()

    def action_mark_paid(self):
        """Une facture annulée ou renversée ne paie plus rien : le paiement
        noté à la main vient d'ailleurs, et la source « Facture » mentirait.
        Le socle pose alors « Autre », que l'agent précise au besoin. Tant que
        la facture porte la cotisation, la garde de `write()` refuse."""
        for rec in self:
            if rec.payment_source == "invoice" and not rec._invoice_drives():
                rec.payment_source = False
        return super().action_mark_paid()

    # ------------------------------------------------------------------
    # La facture
    # ------------------------------------------------------------------

    def action_create_invoice(self):
        """Facturer la cotisation.

        La facture est VALIDÉE tout de suite, pas laissée en brouillon :

        * rien ne s'y décide : l'article vient de la catégorie et le montant de
          l'adhésion. Un brouillon ne servirait qu'à attendre un clic de plus ;
        * un brouillon n'a pas de numéro, ne paraît pas au portail et ne se paie
          pas en ligne (Odoo n'offre le paiement que sur une facture validée) :
          le renouvellement en ligne en a besoin, et l'agent aussi le jour où il
          envoie le lien de paiement ;
        * validée, la facture est une créance : la liste des cotisations à
          recevoir est juste dès aujourd'hui.

        Une erreur se corrige comme sur toute facture, par un avoir ; l'adhésion
        redevient alors « à payer » et se facture de nouveau.

        L'agent n'a pas besoin des droits de facturation : la facture se crée en
        superutilisateur, après le contrôle de son droit d'écrire l'adhésion.
        """
        self.check_access("write")
        moves = self.env["account.move"]
        for rec in self:
            moves |= rec._create_invoice()
        if len(moves) == 1 and self.env["account.move"].has_access("read"):
            return {
                "type": "ir.actions.act_window",
                "res_model": "account.move",
                "res_id": moves.id,
                "view_mode": "form",
                "views": [(self.env.ref("account.view_move_form").id, "form")],
            }
        return True

    def _automatic_invoice_allowed(self):
        """Le parcours automatique (renouvellement au portail, « Payer en ligne »
        du formulaire public) peut-il valider la facture ?

        🔴 La même garde que le bouton « Facturer » de l'agent, que ces parcours
        appellent en superutilisateur : une personne qui ne se facture pas dans
        Odoo (`_invoice_refusal`), ou une cotisation qui diffère de celle de la
        catégorie, ne reçoit pas de facture automatique. Sans cela, un agent
        mettrait la cotisation à 1,00 $ et le membre la ferait valider au clic.
        """
        self.ensure_one()
        return not self._invoice_refusal() and not self.currency_id.compare_amounts(
            self.amount, self.type_id.fee)

    def _invoice_refusal(self):
        """Pourquoi la cotisation ne se facture pas dans Odoo, ou rien.

        🔴 Une personne rattachée à une entreprise (une employée inscrite sous
        son employeur) : Odoo porte toute facture au partenaire commercial,
        donc à l'entreprise. La créance serait la sienne, et toute personne de
        l'entreprise qui a accès au portail y lirait la cotisation, donc
        l'adhésion de sa collègue (Loi 25). Le paiement se note autrement, ou
        on détache le contact de l'entreprise.
        """
        self.ensure_one()
        partner = self.partner_id
        if not partner.is_company and partner.commercial_partner_id != partner:
            return _(
                "%(name)s est rattachée à %(company)s : une facture Odoo irait à cette entreprise, et "
                "ses personnes au portail y liraient la cotisation. Notez le paiement autrement "
                "(chèque, virement, paiement en ligne hors facture), ou détachez d'abord le contact "
                "de l'entreprise.", name=partner.name, company=partner.commercial_partner_id.name)
        return False

    def _check_invoiceable(self):
        self.ensure_one()
        refusal = self._invoice_refusal()
        if refusal:
            raise UserError(refusal)
        user = self.env.user
        if not self.env.su and self.currency_id.compare_amounts(self.amount, self.type_id.fee) and not (
                user.has_group("bf_membership.group_membership_manager")
                or user.has_group("account.group_account_invoice")):
            # 🔴 Sans cette garde, l'agent sans droits comptables validerait des
            # factures clients au montant de son choix.
            raise AccessError(_(
                "%(name)s : la cotisation (%(amount)s) diffère de celle de la catégorie (%(fee)s). "
                "Une cotisation particulière se facture par une personne responsable des membres "
                "ou par la comptabilité.",
                name=self.display_name, amount=self.currency_id.format(self.amount),
                fee=self.currency_id.format(self.type_id.fee)))
        if self.state in ("withdrawn", "refused"):
            raise UserError(_("%s : une adhésion retirée ou refusée ne se facture pas.", self.display_name))
        if self.payment_state != "to_pay":
            raise UserError(_("%s : la cotisation est déjà réglée ou exemptée.", self.display_name))
        move = self.invoice_id.sudo()
        if move and move.state != "cancel" and not move._bf_membership_credited():
            raise UserError(_("%(name)s est déjà facturée (%(invoice)s).",
                              name=self.display_name, invoice=move.name))
        if self.currency_id.compare_amounts(self.amount, 0.0) <= 0:
            raise UserError(_("%s : une cotisation nulle ne se facture pas.", self.display_name))

    def _invoice_partner(self):
        """Qui reçoit la facture : TOUJOURS le membre lui-même.

        🔴 Jamais un contact enfant (l'adresse de facturation d'une organisation,
        par `address_get`) : ce contact se rattache à une autre entreprise par
        une simple écriture, et le portail de celle-ci verrait alors la facture
        et son PDF. Au nom du membre, la facture suit le seul contact que la
        garde de rattachement protège (`res.partner.write`).
        """
        self.ensure_one()
        return self.partner_id

    def _invoice_label(self):
        self.ensure_one()
        return " ".join(p for p in (_("Cotisation"), self.type_id.name, self.period_label) if p)

    def _create_invoice(self, salesperson=None):
        """Créer et valider la facture de la cotisation, et la lier.

        `salesperson` : la personne qui suit la facture. Par défaut l'usager
        courant s'il est un employé ; jamais un membre du portail ni le robot
        du formulaire public, dont le nom et l'adresse partiraient sur les
        courriels de la facture.
        """
        self.ensure_one()
        self._check_invoiceable()
        if salesperson is None:
            user = self.env.user
            salesperson = user if not user.share and not user._is_superuser() else self.env["res.users"]
        product = self.type_id._membership_product()
        move = self.env["account.move"].sudo().with_company(self.company_id).create({
            "move_type": "out_invoice",
            "partner_id": self._invoice_partner().id,
            "invoice_user_id": salesperson.id or False,
            "invoice_origin": " ".join(p for p in (self.member_number, self.period_label) if p),
            "invoice_line_ids": [fields.Command.create({
                "product_id": product.id,
                "name": self._invoice_label(),
                "quantity": 1.0,
                "price_unit": self.amount,
            })],
        })
        previous = self.invoice_id.sudo()
        self.sudo().write({"invoice_id": move.id, "payment_source": "invoice"})
        move.action_post()
        body = Markup(_("Cotisation facturée : %s.")) % move._get_html_link(title=move.name)
        if previous:
            body += Markup(" ") + Markup(_("Elle remplace la facture %s, annulée ou renversée.")) % (
                previous._get_html_link(title=previous.name))
        self.sudo().message_post(body=body, subtype_xmlid="mail.mt_note")
        cron = self.env.ref("bf_membership_account.ir_cron_membership_invoice_pdf", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return move

    def _sync_payment_from_invoice(self):
        """Recopier sur l'adhésion ce que dit sa facture.

        Écrit par `write()`, pour que le socle fasse la transition d'état
        (« à payer » vers « en règle », et retour). N'écrit que ce qui change :
        appelé depuis le calcul de l'état de paiement d'une facture, il passe
        souvent sans rien avoir à faire.
        """
        for rec in self.sudo().with_context(**{INVOICE_SYNC_KEY: True}):
            move = rec.invoice_id
            if not move or rec.payment_source != "invoice":
                continue
            settled = move._bf_membership_settled()
            if settled and rec.payment_state != "paid":
                rec.write({
                    "payment_state": "paid",
                    "payment_date": move._bf_membership_payment_date(),
                    "payment_reference": move.name,
                })
            elif not settled and rec.payment_state == "paid":
                rec.write({"payment_state": "to_pay", "payment_date": False, "payment_reference": False})
                issued = rec.sudo().receipt_ids.filtered(lambda r: r.state == "issued")
                if issued:
                    rec.message_post(subtype_xmlid="mail.mt_note", body=_(
                        "La facture %(invoice)s n'est plus payée, et le reçu fiscal %(receipt)s a "
                        "été délivré pour cette cotisation : annulez-le, et gardez-en la copie.",
                        invoice=move.name, receipt=", ".join(issued.mapped("name"))))

    @api.model
    def _cron_invoice_pdf(self, limit=50):
        """Produire le PDF officiel des factures de cotisation qui ne l'ont pas
        encore. N'envoie rien, et ne marque pas la facture « envoyée » : Odoo
        le fait en produisant le PDF, et la comptabilité lirait comme parties
        des factures que personne n'a envoyées.

        🔴 Sans PDF officiel, le portail montre la facture comme « PROFORMA »,
        ce qu'un membre sur le point de payer lit comme « pas une vraie
        facture ». Le PDF ne se produit pas au moment de facturer : wkhtmltopdf
        retient la requête de la personne de longues secondes. Cette tâche,
        déclenchée à chaque facture, le produit juste après.
        """
        since = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        moves = self.sudo().search([
            ("invoice_id", "!=", False),
            ("invoice_id.state", "=", "posted"),
            ("invoice_id.create_date", ">=", since),
        ]).invoice_id
        # 🔴 `invoice_pdf_report_id` n'est pas stocké : un domaine sur lui est
        # écarté avec une simple ligne au journal. Filtré en Python.
        moves = moves.filtered(lambda m: not m.invoice_pdf_report_id)[:limit]
        for move in moves:
            sent = move.is_move_sent
            try:
                with self.env.cr.savepoint():
                    self.env["account.move.send"].sudo()._generate_and_send_invoices(move, sending_methods=[])
                    if move.is_move_sent != sent:
                        move.is_move_sent = sent
            except Exception:  # noqa: BLE001
                _logger.warning("Facture de cotisation %s : PDF officiel non produit", move.name, exc_info=True)

    def action_view_invoice(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.invoice_id.id,
            "view_mode": "form",
            "views": [(self.env.ref("account.view_move_form").id, "form")],
        }

    # ------------------------------------------------------------------
    # Le reçu fiscal
    # ------------------------------------------------------------------

    def _receipt_payment(self):
        """(montant de la cotisation, date de réception) que porte le reçu.

        🔴 Quand une facture porte la cotisation, le reçu certifie l'ENCAISSÉ :
        l'argent reçu sur un compte de liquidité, par les pièces de paiement
        lettrées à la facture (`_bf_membership_cash_received`). Il ne se délivre
        que si cet encaissé égale la cotisation, sur une facture au nom du
        membre qui ne porte que la cotisation, sans taxe, et qu'aucun avoir
        n'a suivie. La date est celle des pièces lettrées. Le montant et la date
        saisis sur l'adhésion ne servent qu'au paiement noté à la main, dont le
        reçu est réservé à la personne responsable des membres ou à la
        comptabilité (`_check_receipt_issuer`).
        """
        self.ensure_one()
        if self.payment_source != "invoice":
            return self.amount, self.payment_date
        move = self.invoice_id.sudo()
        if not move or not move._bf_membership_settled():
            raise UserError(_(
                "%s : la cotisation est notée payée par facture, mais aucune facture payée ne la "
                "porte. Aucun reçu fiscal.", self.display_name))
        # 🔴 La facture est TOUJOURS au nom du membre lui-même, pour la catégorie :
        # une adhésion dont le membre ou la catégorie aurait changé après coup,
        # ou une facture passée à un autre client, ne donne rien.
        product = self.type_id.product_id
        if (move.partner_id != self.partner_id
                or move.commercial_partner_id != self.partner_id.commercial_partner_id
                or not product or product not in move.invoice_line_ids.product_id):
            raise UserError(_(
                "%(name)s : la facture %(invoice)s n'est pas celle de ce membre pour cette "
                "catégorie. Aucun reçu fiscal.", name=self.display_name, invoice=move.name))
        # Une seule ligne, de l'article, en quantité 1, au montant de l'adhésion.
        if not self._fee_only_invoice(move):
            raise UserError(_(
                "%(name)s : la facture %(invoice)s n'est pas une facture de cotisation seule. Le "
                "reçu de cotisation exige une seule ligne, de l'article de la catégorie, en "
                "quantité 1, au montant de l'adhésion (%(amount)s).",
                name=self.display_name, invoice=move.name, amount=self.currency_id.format(self.amount)))
        # Sans taxe : le reçu certifie un don en argent, et le module ne départage
        # pas une taxe d'une cotisation.
        if self.currency_id.compare_amounts(move.amount_total, self.amount):
            raise UserError(_(
                "%(name)s : la facture %(invoice)s porte des taxes ; le reçu de cotisation ne se "
                "délivre que sur une cotisation facturée sans taxe.", name=self.display_name, invoice=move.name))
        cash, reason = move._bf_membership_cash_received()
        if not reason and self._fee_credited(move):
            reason = _("un avoir client au même membre suit la facture")
        if not reason and self.currency_id.compare_amounts(cash, self.amount):
            reason = _("encaissé %(cash)s pour une cotisation de %(amount)s",
                       cash=self.currency_id.format(cash), amount=self.currency_id.format(self.amount))
        if reason:
            raise UserError(_(
                "%(name)s : le paiement ne couvre pas la cotisation en argent reçu (%(reason)s). "
                "Aucun reçu fiscal.", name=self.display_name, reason=reason))
        return self.amount, move._bf_membership_payment_date()

    def _fee_credited(self, move):
        """Un avoir client validé suit la facture : un avoir de la facture, ou
        TOUT avoir client au même partenaire commercial, daté de la facture ou
        après, avec ou sans l'article de cotisation.

        🔴 Règle prudente : un avoir saisi à part, sur une ligne sans article
        (un libellé et un compte de revenu), puis remboursé, rend au membre une
        part de ce qu'il a versé, et l'encaissé lettré à la facture ne le dit
        pas. Un reçu refusé vaut mieux qu'un reçu faux (README, limites
        connues)."""
        self.ensure_one()
        refunds = self.env["account.move"].sudo().search([
            ("move_type", "=", "out_refund"), ("state", "=", "posted"),
            "|", ("reversed_entry_id", "=", move.id),
            ("commercial_partner_id", "=", move.commercial_partner_id.id),
        ])
        return bool(refunds.filtered(
            lambda r: r.reversed_entry_id == move or (r.invoice_date or r.date) >= (move.invoice_date or move.date)))

    def _invoice_backed(self):
        """Une vraie facture, payée, porte la cotisation : la décision se prend
        sur la facture, jamais sur la source notée, qu'un agent écrit."""
        self.ensure_one()
        return self._invoice_drives() and self.invoice_id.sudo()._bf_membership_settled()

    def _fee_only_invoice(self, move):
        """Vrai si `move` ne porte, hors taxes, que la cotisation de cette
        adhésion : une seule ligne de produit, de l'article de la catégorie, en
        quantité 1, dont le sous-total égale la cotisation, dans la devise de la
        société."""
        self.ensure_one()
        lines = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
        if len(lines) != 1 or lines.product_id != self.type_id.product_id or move.currency_id != self.currency_id:
            return False
        rounding = lines.product_uom_id.rounding or 0.0001
        return (not float_compare(lines.quantity, 1.0, precision_rounding=rounding)
                and not self.currency_id.compare_amounts(lines.price_subtotal, self.amount))

    def _check_receipt_issuer(self):
        """Hors facture payée, le reçu se délivre (et s'annule) par la personne
        responsable des membres ou par la comptabilité.

        🔴 Le montant et la date d'un paiement noté à la main (chèque, Zeffy,
        virement) sont saisis par l'agent, sans pièce comptable derrière : un
        reçu officiel bâti sur eux se vérifie par quelqu'un qui en répond. Le
        cercle se décide sur la facture réelle (`_invoice_backed`).
        """
        self.ensure_one()
        if self.env.su or self._invoice_backed():
            return
        user = self.env.user
        if user.has_group("bf_membership.group_membership_manager") or user.has_group(
                "account.group_account_invoice"):
            return
        raise AccessError(_(
            "%s : la cotisation n'a pas été payée par une facture. Le reçu fiscal se délivre alors "
            "par une personne responsable des membres ou par la comptabilité.", self.display_name))

    def _check_receipt_issuer_company(self):
        """Ce que l'article 3501(1) du Règlement exige de lire sur l'organisme :
        son adresse au Canada (b), son numéro d'enregistrement (c) et le lieu
        de délivrance (e). Le refus nomme tout ce qui manque à la fois."""
        self.ensure_one()
        company = self.company_id
        missing = []
        if not company.street:
            missing.append(_("la rue"))
        if not company.city:
            missing.append(_("la ville"))
        if not company.zip:
            missing.append(_("le code postal"))
        if company.country_id.code != "CA":
            missing.append(_("le pays (Canada)"))
        if not company.membership_charity_number:
            missing.append(_("le numéro d'enregistrement de l'organisme de bienfaisance"))
        if not (company.membership_receipt_place or company.city):
            missing.append(_("le lieu de délivrance (réglage des Membres, à défaut la ville de la société)"))
        if missing:
            raise UserError(_(
                "Le reçu fiscal exige l'adresse au Canada de l'organisme, son numéro d'enregistrement "
                "et le lieu de délivrance (article 3501 du Règlement de l'impôt sur le revenu). "
                "À compléter dans la fiche de la société ou les réglages des Membres : %s.",
                ", ".join(missing)))

    def _check_receipt_allowed(self):
        """Un reçu officiel ne se délivre que sur une cotisation payée, d'une
        catégorie admissible, par une personne qui en a le droit, avec tout ce
        que l'article 3501 du Règlement de l'impôt sur le revenu exige d'y
        lire."""
        self.ensure_one()
        if self.payment_state != "paid":
            raise UserError(_("%s : un reçu fiscal ne se délivre que sur une cotisation payée.",
                              self.display_name))
        if not self.type_id.receipt_eligible:
            raise UserError(_("La catégorie « %s » ne donne pas de reçu fiscal.", self.type_id.name))
        self._check_receipt_issuer()
        amount, date_received = self._receipt_payment()
        if self.currency_id.compare_amounts(self.type_id._eligible_amount(amount)[0], 0.0) <= 0:
            raise UserError(_(
                "%s : l'avantage reçu dépasse 80 %% de la cotisation, ou la cotisation est nulle. "
                "Aucun reçu fiscal (politique CSP-M05 de l'ARC).", self.display_name))
        self._check_receipt_issuer_company()
        if not self.company_id.membership_receipt_signer:
            raise UserError(_(
                "Nommez la personne autorisée à signer les reçus dans les réglages des Membres."))
        if not date_received:
            raise UserError(_("%s : la date du paiement manque ; le reçu la porte.", self.display_name))
        donor = self.sudo()
        if not (donor.donor_name and donor.donor_address):
            raise UserError(_(
                "Le reçu porte le nom et l'adresse du membre figés au paiement, et l'adresse de %s "
                "manquait alors. Complétez-la, puis une personne responsable des membres met à jour "
                "le nom et l'adresse au reçu.", self.partner_id.name))

    def _receipt_vals(self, replaces=None):
        self.ensure_one()
        company = self.company_id
        amount, date_received = self._receipt_payment()
        eligible, disregarded = self.type_id._eligible_amount(amount)
        return {
            "membership_id": self.id,
            "partner_id": self.partner_id.id,
            "company_id": company.id,
            "date_received": date_received,
            "date_issued": fields.Date.context_today(self),
            "gift_amount": amount,
            "advantage_amount": self.type_id.advantage_amount,
            "advantage_disregarded": disregarded,
            "advantage_description": self.type_id.advantage_description or False,
            "eligible_amount": eligible,
            # Figés au paiement : jamais relus sur le contact d'aujourd'hui.
            "donor_name": self.sudo().donor_name,
            "donor_address": self.sudo().donor_address,
            "org_name": company.partner_id.name,
            "org_address": company.partner_id._display_address(without_company=True),
            "registration_number": company.membership_charity_number,
            "place": company.membership_receipt_place or company.city or False,
            "signer_name": company.membership_receipt_signer,
            "signer_title": company.membership_receipt_signer_title or False,
            # Réservée au responsable des membres : recopiée en superutilisateur.
            "signature": company.sudo().membership_receipt_signature or False,
            "replaces_id": replaces.id if replaces else False,
        }

    def _issue_receipt(self, replaces=None):
        """Créer le reçu (et son numéro) si la cotisation n'en a pas déjà un.

        Appelé après le contrôle des droits de l'usager sur l'adhésion : le
        reçu se crée en superutilisateur, parce que PERSONNE n'écrit un reçu à
        la main (droits de lecture seulement).
        """
        self.ensure_one()
        receipt = self.sudo().receipt_ids.filtered(lambda r: r.state == "issued")[:1]
        if receipt:
            return receipt
        self._check_receipt_allowed()
        vals = self._receipt_vals(replaces=replaces)
        Receipt = self.env["bf.membership.receipt"].sudo().with_company(self.company_id)
        vals["name"] = Receipt._next_number(self.company_id, vals["date_issued"])
        receipt = Receipt.create(vals)
        self.sudo().message_post(subtype_xmlid="mail.mt_note", body=Markup(_(
            "Reçu fiscal %s préparé.")) % receipt._get_html_link(title=receipt.name))
        return receipt

    def action_issue_receipt(self):
        """Délivrer le reçu fiscal, ou son duplicata s'il a déjà été délivré."""
        self.ensure_one()
        self.check_access("write")
        receipt = self._issue_receipt()
        return self.env.ref("bf_membership_account.action_report_membership_receipt").report_action(
            receipt.with_env(self.env))

    def action_view_receipts(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_membership_account.action_membership_receipt")
        action["domain"] = [("membership_id", "=", self.id)]
        return action
