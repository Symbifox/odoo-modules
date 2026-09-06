"""mail.compose.message override: keep explicit Cc / Bcc context defaults.

We rely on ``mail_composer_cc_bcc`` (already installed) for the Cc / Bcc
plumbing on the composer. Its compute on ``partner_cc_ids`` / ``partner_bcc_ids``
resets those lists to the company default whenever ``composition_mode`` /
``model`` / ``res_ids`` are touched, which happens on every fresh wizard.
That wipes out the values our reply-all dispatcher passes via
``default_partner_cc_ids`` in context.

This override honors those context defaults and skips the inherited
recompute when they are present, so the bf.email Reply-All flow can pre-fill
Cc with the other thread participants.
"""

from dateutil.relativedelta import relativedelta
from lxml import html

from odoo import _, api, exceptions, fields, models

from .bf_recipient_group import PARAM_ENABLED


class MailComposeMessage(models.TransientModel):
    _name = "mail.compose.message"
    _inherit = ["mail.compose.message", "bf.chatter.target.mixin"]

    # Renseigné par ``bf.email.inbox_compose`` : c'est le seul marqueur qui
    # distingue « je compose depuis la boîte de réception » de n'importe quel
    # composeur de chatter de l'instance. Le champ cible n'est montré, et le
    # re-ciblage n'a lieu, que dans ce cas.
    bf_compose_shell_id = fields.Integer(
        string="Brouillon bf.email",
        default=lambda self: self.env.context.get(
            "bf_email_compose_shell_id", 0),
    )

    def _bf_retarget_to_chatter(self):
        """Repointer le composeur sur la fiche choisie, avant tout envoi.

        Le courriel composé depuis la boîte part d'une ligne ``bf.email`` qui
        se sert de fil à elle-même, faute de savoir où le classer. Quand
        l'usager désigne une fiche, autant poster directement sur son chatter :
        le message y naît au bon endroit, avec ses abonnés et son fil, plutôt
        que d'être déplacé après coup.

        ⚠️ ``subject``, ``body`` ET **les destinataires** sont des calculs
        stockés qui dépendent de ``model`` / ``res_ids``. Toucher à la cible
        **efface le corps** (``_compute_body`` remet False en l'absence de
        gabarit), réécrit l'objet, et — le plus coûteux —
        ``_compute_partner_ids`` repasse à ``False`` : sans gabarit ni parent,
        il ne recalcule pas les destinataires, **il les vide**. On les relit
        donc avant, et on les réécrit après : une écriture explicite sur un
        champ calculé le retire de la file de recalcul, ce qu'un simple
        `write` groupé ne garantit pas.

        🔴 Sans la reprise des destinataires, le courriel partait **à
        personne** : le message naissait bien sur la fiche choisie, visible
        dans le chatter comme n'importe quel envoi, mais sans un seul
        ``mail.notification``. Rien ne le signalait, ni à l'écran ni au
        journal. Relevé le 2026-08-31 : sept messages en trois mois, dont
        quatre vrais courriels — le plus récent demandait à quelqu'un s'il
        était toujours disponible pour une rencontre qui commençait.
        """
        for wizard in self:
            if not wizard.bf_compose_shell_id or not wizard.target_reference:
                continue
            target = wizard._get_chatter_target("write")
            if target._name == wizard.model and target.id in (
                    wizard._evaluate_res_ids() or []):
                continue
            keep = {
                "subject": wizard.subject,
                "body": wizard.body,
                "partner_ids": [(6, 0, wizard.partner_ids.ids)],
                "partner_cc_ids": [(6, 0, wizard.partner_cc_ids.ids)],
                "partner_bcc_ids": [(6, 0, wizard.partner_bcc_ids.ids)],
            }
            wizard.write({
                "model": target._name,
                "res_ids": repr([target.id]),
            })
            wizard.write(keep)

    # ------------------------------------------------------------------
    # Identité d'expédition
    # ------------------------------------------------------------------
    bf_identity_id = fields.Many2one(
        "bf.email.identity",
        # « De » et non « Envoyer en tant que » : c'est le nom que la
        # personne cherche des yeux, et Odoo cache son propre `email_from` en
        # mode commentaire, donc la place est libre.
        string="De",
        domain="[('id', 'in', bf_identity_allowed_ids)]",
        help="L'adresse qui apparaîtra dans le « De ». Seules vos identités "
             "vérifiées sont proposées.",
    )
    bf_identity_allowed_ids = fields.Many2many(
        "bf.email.identity",
        compute="_compute_bf_identity_allowed_ids",
        string="Identités disponibles",
    )
    # ⚠️ L'évaluateur Python du client web ne connaît pas ``len``. Une vue qui
    # écrit ``len(bf_identity_allowed_ids) < 2`` lève un EvalError et le
    # composeur ne s'affiche plus du tout — la boîte de dialogue meurt au
    # rendu, sur toute l'instance. Le compte doit donc arriver côté client
    # comme un entier déjà calculé, qu'une simple comparaison suffit à lire.
    bf_identity_count = fields.Integer(
        compute="_compute_bf_identity_allowed_ids",
        string="Nombre d'identités disponibles",
    )
    # ⚠️ `depends_context` NE SUFFIT PAS, et le défaut est silencieux.
    # Un calcul sans AUCUNE dépendance de champ n'est jamais déclenché pendant
    # un `onchange` : le client reçoit la valeur par défaut, zéro. La condition
    # `invisible="bf_identity_count < 2"` était donc vraie à l'ouverture du
    # composeur, et le sélecteur « Envoyer en tant que » restait invisible pour
    # tout le monde, depuis qu'il existe. Rien ne se voyait côté serveur : une
    # lecture directe et un `new()` rendent le bon compte, seul le chemin
    # `onchange` mentait. Mesuré le 2026-09-03 : lecture 3, onchange 0.
    # `composition_mode` est le porteur choisi parce qu'il est toujours dans la
    # vue et toujours renseigné ; la valeur ne dépend pas de lui, c'est
    # l'accroche qui fait courir le calcul.
    @api.depends("composition_mode")
    @api.depends_context("uid")
    def _compute_bf_identity_allowed_ids(self):
        usable = self.env["bf.email.identity"]._usable_for(self.env.user)
        count = len(usable)
        for composer in self:
            composer.bf_identity_allowed_ids = usable
            composer.bf_identity_count = count

    # ------------------------------------------------------------------
    # Aperçu de la signature
    # ------------------------------------------------------------------
    bf_signature_preview = fields.Html(
        string="Signature",
        compute="_compute_bf_signature_preview",
        sanitize=False,
        readonly=True,
        help="Ce qui sera ajouté au bas du courriel à l'envoi. En lecture "
             "seule : la signature n'entre pas dans le corps, sinon le "
             "destinataire en recevrait deux.",
    )

    @api.depends("bf_identity_id", "email_add_signature", "template_id")
    @api.depends_context("uid")
    def _compute_bf_signature_preview(self):
        """Montrer la signature que l'envoi posera — sans l'écrire dans le corps.

        Le composeur s'ouvre nu depuis la 18.0.11.9.0, et c'est voulu : la
        signature est ajoutée une seule fois au rendu du courriel. Restait
        qu'à l'écran plus rien ne disait qu'elle partirait. Ce champ le dit,
        en rejouant la MÊME résolution que
        ``mail_thread._notify_by_email_prepare_rendering_context`` :

        1. rien du tout quand ``email_add_signature`` est faux — c'est le cas
           dès qu'un gabarit est choisi, Odoo coupe alors la signature ;
        2. la signature de l'identité d'expédition si elle en a une ;
        3. sinon celle de la personne (déjà rendue aux couleurs de sa société
           par ``bf_multi_company_email``) ;
        4. en dernier ressort le repli de société, quand rien d'autre n'existe.

        ⚠️ Un aperçu qui mentirait serait pire que pas d'aperçu : toute
        divergence avec l'ordre ci-dessus est un défaut, pas un détail
        cosmétique.
        """
        # En mode « brouillon » la signature est DANS le corps, sous les yeux :
        # un aperçu la montrerait deux fois à l'écran.
        if self.env["bf.email"]._signature_placement() == "brouillon":
            for composer in self:
                composer.bf_signature_preview = False
            return
        Identity = (self.env["bf.email.identity"]
                    if "bf.email.identity" in self.env else None)
        company = self.env.company
        repli = ""
        if "brand_email_signature_default" in company._fields:
            repli = company.sudo().brand_email_signature_default or ""
        for composer in self:
            signature = ""
            if composer.email_add_signature:
                identity = composer.bf_identity_id
                if not identity and Identity is not None:
                    identity = Identity.sudo()._for_sender(
                        composer.email_from, self.env.user)
                # Même cascade que le brouillon et que l'envoi : identité,
                # puis société du compte de l'identité, puis la signature de
                # la société principale.
                signature = self.env["bf.email"]._signature_for_identity(
                    identity)
            if not (signature or "").strip():
                signature = repli
            composer.bf_signature_preview = signature or False

    @api.onchange("bf_identity_id")
    def _onchange_bf_identity_id(self):
        """Suivre l'identité choisie : le « De », et la signature du corps.

        ⚠️ **L'hypothèse d'hier n'est plus vraie.** Ce raccord avait été retiré
        en 18.0.11.9.0, quand le composeur s'ouvrait nu : la signature n'entrait
        pas dans le corps, elle était posée à l'envoi, donc rien n'était à
        échanger ici. La 18.0.11.13.0 l'a **remise dans le brouillon** et ce
        raccord n'a jamais été rétabli : depuis, changer d'adresse changeait le
        « De » et laissait la signature de l'adresse précédente juste en
        dessous.

        La substitution d'alors était dite fragile parce qu'elle cherchait dans
        un corps **assaini** le bloc exact qu'elle avait posé **non assaini**.
        Celle-ci ne compare plus des chaînes : elle localise le bloc par sa
        **classe** et ne remplace que du texte à texte.
        """
        if not self.bf_identity_id:
            return
        self.email_from = self.bf_identity_id.email_formatted
        self._bf_swap_body_signature()

    @staticmethod
    def _bf_signature_text(el):
        """Le texte d'un bloc, espaces normalisés — insensible à l'assainissement."""
        return " ".join((el.text_content() or "").split())

    def _bf_swap_body_signature(self):
        """Remplacer le bloc signature du corps par celui de l'identité choisie.

        Ne fait rien, et c'est voulu, dans trois cas :

        - la signature ne vit pas dans le corps (réglage « à l'envoi ») ;
        - le corps ne porte pas le marqueur, ou en porte plusieurs — on ne
          devine pas lequel est le bon ;
        - le texte du bloc ne correspond à la signature d'**aucune** des
          identités de la personne, ce qui veut dire qu'elle l'a modifié. Un
          échange silencieux effacerait alors son travail : mieux vaut laisser
          une signature à corriger à la main qu'en emporter une réécrite.
        """
        self.ensure_one()
        Bf = self.env["bf.email"]
        if Bf._signature_placement() != "brouillon":
            return
        corps = self.body or ""
        if Bf.SIGNATURE_MARKER not in corps:
            return
        try:
            racine = html.fragment_fromstring(corps, create_parent="div")
        except Exception:  # pragma: no cover - corps illisible, on s'abstient
            return
        blocs = racine.find_class(Bf.SIGNATURE_MARKER)
        if len(blocs) != 1:
            return
        bloc = blocs[0]

        connus = set()
        for ident in self.bf_identity_allowed_ids | self.bf_identity_id:
            brut = Bf._compose_signature_block_for_user(identity=ident)
            if not brut:
                continue
            try:
                connus.add(self._bf_signature_text(
                    html.fragment_fromstring(brut, create_parent="div")))
            except Exception:  # pragma: no cover
                continue
        # La signature de la personne, sans identité : c'est celle qu'un
        # composeur ouvert avant tout choix a pu poser.
        brut_defaut = Bf._compose_signature_block_for_user()
        if brut_defaut:
            try:
                connus.add(self._bf_signature_text(
                    html.fragment_fromstring(brut_defaut, create_parent="div")))
            except Exception:  # pragma: no cover
                pass
        if self._bf_signature_text(bloc) not in connus:
            return

        neuf_brut = Bf._compose_signature_block_for_user(
            identity=self.bf_identity_id)
        parent = bloc.getparent()
        if not neuf_brut:
            parent.remove(bloc)
        else:
            neuf = html.fragment_fromstring(neuf_brut)
            parent.replace(bloc, neuf)
        self.body = "".join(
            [racine.text or ""]
            + [html.tostring(enfant, encoding="unicode") for enfant in racine]
        )

    def _prepare_mail_values(self, res_ids):
        """Faire descendre l'identité jusqu'au message, après la fusion.

        ⚠️ Le raccord ne peut PAS être ``_prepare_mail_values_static``.
        ``_prepare_mail_values`` construit ``dict(base_values, **additional)``,
        et pour le mode « commentaire » ``additional`` vient de
        ``_prepare_mail_values_rendered``, qui pose son propre ``email_from``
        (le champ du composeur). Un ``email_from`` déposé côté statique est
        donc **écrasé sans un mot** : le courriel repart sous l'adresse du
        compte Odoo et rien ne le signale. Mesuré sur banc avant de le
        corriger.

        On stampe donc après la fusion, ce qui couvre du même geste le chemin
        commentaire, le rendu et le publipostage.

        Uniquement si une identité a été **explicitement** choisie : imposer un
        ``email_from`` partout changerait le « De » de tous les composeurs de
        l'instance, y compris ceux qui postent au nom d'un autre auteur.
        ``_message_compute_author`` retourne ``author_id`` et ``email_from``
        inchangés dès que les deux sont fournis, donc un défaut mal calculé
        s'imposerait en silence.
        """
        values_all = super()._prepare_mail_values(res_ids)
        identity = self.bf_identity_id
        if not identity:
            return values_all
        for values in values_all.values():
            values["email_from"] = identity.email_formatted
            if identity.mail_server_id:
                values["mail_server_id"] = identity.mail_server_id.id
        return values_all

    def _action_send_mail(self, auto_commit=False):
        self._bf_check_identity()
        # ⚠️ AVANT le re-ciblage : celui-ci relit et réécrit les destinataires,
        # et une fiche-groupe qui lui échapperait partirait telle quelle, sans
        # adresse, donc à personne. Le dépliage applique aussi le plafond, la
        # seule barrière contre l'envoi de masse involontaire.
        self._bf_expand_recipient_groups()
        self._bf_retarget_to_chatter()
        return super()._action_send_mail(auto_commit=auto_commit)

    def _bf_check_identity(self):
        """Une identité qu'on n'a pas le droit de porter ne part pas.

        Le domaine du champ borne déjà la liste dans l'écran, mais un appel
        RPC ne passe pas par l'écran. La garde est ici, sous l'envoi mais
        avant lui — au-delà, le ``mail.mail`` existe et un rollback ne
        rappelle pas un courriel.
        """
        for composer in self.filtered("bf_identity_id"):
            identity = composer.bf_identity_id.sudo()
            if identity.user_id != self.env.user:
                raise exceptions.UserError(_(
                    "« %s » n'est pas une de vos identités d'expédition.",
                    identity.display_name))
            if not identity.verified or not identity.active:
                raise exceptions.UserError(_(
                    "« %s » n'est pas vérifiée : un administrateur courriel "
                    "doit l'autoriser avant qu'elle puisse servir.",
                    identity.display_name))

    def action_schedule_message(self, scheduled_date=False):
        # Le report lit lui aussi `model` / `res_ids` pour bâtir la
        # mail.scheduled.message : sans ce crochet, un envoi programmé
        # atterrirait sur le brouillon et non sur la fiche choisie.
        # Et le dépliage doit avoir lieu MAINTENANT : la programmation fige les
        # destinataires, un groupe non déplié partirait vide dans trois jours.
        self._bf_expand_recipient_groups()
        self._bf_retarget_to_chatter()
        return super().action_schedule_message(scheduled_date=scheduled_date)

    # ------------------------------------------------------------------
    # « Enregistrer comme brouillon »
    # ------------------------------------------------------------------
    # Années d'avance de la sentinelle. Le noyau EXIGE une date d'envoi et la
    # refuse dans le passé : un brouillon doit donc en porter une. On la met
    # assez loin pour qu'aucun cron ne la rattrape de notre vivant
    # professionnel — et `bf_is_draft` fait le vrai travail, la date n'est
    # qu'une formalité imposée par le schéma.
    _BF_DRAFT_SENTINEL_YEARS = 5

    def action_bf_save_as_draft(self):
        """Garder le courriel sans l'envoyer, et fermer le composeur.

        Il n'existait aucun « Enregistrer » : le composeur est un
        ``TransientModel``, le refermer perdait tout, et la seule façon de
        garder un texte était de le PROGRAMMER en saisissant une date lointaine
        à la main. Ce bouton fait ce geste-là en un clic.

        Le brouillon atterrit dans le dossier « Brouillons » de la boîte, d'où
        « Envoyer maintenant » le fait partir. Rien ne part d'ici.
        """
        self.ensure_one()
        # Même garde que l'envoi : une identité qu'on n'a pas le droit de
        # porter ne doit pas plus s'écrire dans un brouillon, sinon le refus
        # n'arrive qu'au moment de l'envoi, des jours plus tard.
        self._bf_check_identity()
        sentinelle = fields.Datetime.now() + relativedelta(
            years=self._BF_DRAFT_SENTINEL_YEARS)
        # ⚠️ `bf_save_as_draft` et non `default_bf_is_draft` : `create` du
        # noyau passe par `clean_context`, qui efface les clés `default_*`.
        return self.with_context(bf_save_as_draft=True).action_schedule_message(
            scheduled_date=sentinelle)

    @api.depends(
        "composition_mode",
        "model",
        "parent_id",
        "res_domain",
        "res_ids",
        "template_id",
    )
    def _compute_partner_cc_bcc_ids(self):
        ctx = self.env.context
        cc_default = ctx.get("default_partner_cc_ids")
        bcc_default = ctx.get("default_partner_bcc_ids")
        if cc_default is not None or bcc_default is not None:
            for composer in self:
                if cc_default is not None:
                    composer.partner_cc_ids = cc_default
                if bcc_default is not None:
                    composer.partner_bcc_ids = bcc_default
            self._bf_restore_groups(("cc", "bcc"))
            return
        res = super()._compute_partner_cc_bcc_ids()
        # Même raison que pour `partner_ids` : ce calcul se redéclenche au
        # choix d'un gabarit et remet les listes à leur valeur de société.
        self._bf_restore_groups(("cc", "bcc"))
        return res

    # ==================================================================
    # Groupes de destinataires
    # ==================================================================
    # Le geste visé : taper « Équipe projet » dans « À » et voir les vingt
    # personnes apparaître, comme une liste de distribution Outlook. Trois
    # chemins mènent au même endroit, et c'est voulu :
    #
    #   1. l'onchange, pour que le dépliage se voie À L'ÉCRAN (on ne signe pas
    #      un envoi dont on ne connaît pas les destinataires) ;
    #   2. les calculs de `partner_ids` et `partner_cc_ids`, parce qu'un
    #      gabarit choisi APRÈS le groupe les remet à zéro — le champ
    #      `bf_recipient_group_ids` n'est PAS calculé, il sert de mémoire ;
    #   3. l'envoi, dernier filet : un appel RPC ne passe ni par l'écran ni
    #      par l'onchange, et une fiche-groupe restée dans la liste écrirait
    #      à personne.
    _BF_GROUP_FIELD = {
        "to": "partner_ids",
        "cc": "partner_cc_ids",
        "bcc": "partner_bcc_ids",
    }

    bf_recipient_group_ids = fields.Many2many(
        "bf.recipient.group",
        "bf_compose_recipient_group_rel", "wizard_id", "group_id",
        string="Groupes de destinataires",
    )
    # ⚠️ Ancré sur `composition_mode` et non sur le seul `depends_context` :
    # un calcul sans AUCUNE dépendance de champ n'est jamais déclenché pendant
    # un `onchange`, le client reçoit le défaut (faux) et la ligne reste
    # cachée pour tout le monde. C'est exactement ce qui est arrivé au
    # sélecteur « De » en 11.18.0.
    bf_recipient_groups_enabled = fields.Boolean(
        string="Groupes en service",
        compute="_compute_bf_recipient_groups_enabled",
    )

    @api.depends("composition_mode")
    def _compute_bf_recipient_groups_enabled(self):
        enabled = self.env["bf.recipient.group"]._groups_enabled()
        for composer in self:
            composer.bf_recipient_groups_enabled = enabled

    def _bf_expand_recipient_groups(self):
        """Remplacer les fiches-groupes par leurs membres. Plafond appliqué.

        Rend le nombre total de destinataires, pour que l'appelant puisse
        avertir quand la liste devient grande.

        ⚠️ Ne pas essayer de compter les AJOUTS pour ne prévenir qu'une fois :
        la première lecture de ``partner_ids`` déclenche son calcul, donc
        ``_bf_restore_groups``, donc le dépliage a DÉJÀ eu lieu quand on
        mesure. Le compte d'ajouts rend zéro et l'avertissement ne part
        jamais. Mesuré le 2026-09-03, deux tests rouges.
        """
        Group = self.env["bf.recipient.group"]
        total = 0
        for composer in self:
            if not Group._groups_enabled():
                # Fonction dormante : on ne déplie pas, et surtout on ne
                # laisse pas passer une fiche-groupe qui partirait sans
                # adresse. Le refus est explicite, il ne se devine pas.
                if composer.bf_recipient_group_ids or (
                        composer.partner_ids | composer.partner_cc_ids
                        | composer.partner_bcc_ids).filtered(
                            "bf_recipient_group_id"):
                    raise exceptions.UserError(_(
                        "Les groupes de destinataires ne sont pas encore en "
                        "service sur cette instance. Un administrateur les "
                        "active dans les paramètres système "
                        "(%s).", PARAM_ENABLED))
                continue
            buckets = {
                key: composer[field]
                for key, field in self._BF_GROUP_FIELD.items()
            }
            groups = composer.bf_recipient_group_ids
            # Une fiche-groupe déposée dans « À », « Cc » ou « Cci » vaut
            # sélection du groupe : c'est le geste Outlook.
            for key, recs in list(buckets.items()):
                proxies = recs.filtered("bf_recipient_group_id")
                if proxies:
                    buckets[key] = recs - proxies
                    groups |= proxies.bf_recipient_group_id
            if not groups:
                continue
            if composer.composition_mode == "mass_mail":
                raise exceptions.UserError(_(
                    "Les groupes de destinataires ne servent pas à l'envoi de "
                    "masse : celui-ci écrit à chaque personne séparément et "
                    "n'a pas besoin d'eux. Passez par Marketing par courriel."))

            resolved = self.env["res.partner"].browse()
            for group in groups:
                members = group._resolve_partners()
                resolved |= members
                buckets[group.recipient_field] |= members
            # ⚠️ Le plafond vaut aussi pour l'UNION : trois groupes de
            # quarante passent chacun leur contrôle et font cent vingt
            # destinataires.
            maximum = Group._max_recipients()
            if len(resolved) > maximum:
                raise exceptions.UserError(_(
                    "Ces groupes désignent ensemble %(nb)s destinataires, "
                    "au-delà de la limite de %(max)s.",
                    nb=len(resolved), max=maximum))

            composer.bf_recipient_group_ids = groups
            for key, field in self._BF_GROUP_FIELD.items():
                if composer[field] != buckets[key]:
                    composer[field] = buckets[key]
            total = max(total, len(
                composer.partner_ids | composer.partner_cc_ids
                | composer.partner_bcc_ids))
        return total

    def _bf_restore_groups(self, keys):
        """Recoller les membres après un recalcul qui a vidé les destinataires.

        ⚠️ Appelé depuis un CALCUL : il ne doit rien lever. Un groupe devenu
        trop gros est laissé de côté ici, et c'est l'envoi qui refusera, avec
        son message. Taire l'erreur ne fait donc jamais partir un courriel.
        """
        if not self.env["bf.recipient.group"]._groups_enabled():
            return
        for composer in self:
            for group in composer.bf_recipient_group_ids:
                if group.recipient_field not in keys:
                    continue
                try:
                    members = group._resolve_partners()
                except (exceptions.UserError, exceptions.ValidationError):
                    continue
                field = self._BF_GROUP_FIELD[group.recipient_field]
                composer[field] |= members

    @api.onchange("bf_recipient_group_ids", "partner_ids",
                  "partner_cc_ids", "partner_bcc_ids")
    def _onchange_bf_recipient_groups(self):
        total = self._bf_expand_recipient_groups()
        seuil = self.env["bf.recipient.group"]._confirm_above()
        if self.bf_recipient_group_ids and total > seuil:
            # Pas une erreur : un avertissement, au moment où la liste vient
            # d'apparaître et où la corriger ne coûte rien. Le composeur envoie
            # UN courriel, où chaque destinataire lit l'adresse des autres.
            return {"warning": {
                "title": _("Beaucoup de destinataires"),
                "message": _(
                    "Ce courriel partira à %(nb)s personnes, et chacune verra "
                    "l'adresse de toutes les autres. Relisez la liste, ou "
                    "versez le groupe en Cci.", nb=total),
            }}

    @api.depends("composition_mode", "model", "parent_id", "res_domain",
                 "res_ids", "template_id")
    def _compute_partner_ids(self):
        super()._compute_partner_ids()
        self._bf_restore_groups(("to",))
