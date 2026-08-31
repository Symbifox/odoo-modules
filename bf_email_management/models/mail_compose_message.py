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

from odoo import _, api, exceptions, fields, models


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

        ⚠️ ``subject`` et ``body`` sont des calculs stockés qui dépendent de
        ``model`` / ``res_ids``. Toucher à la cible **efface le corps**
        (``_compute_body`` remet False en l'absence de gabarit) et réécrit
        l'objet. On les relit donc avant, et on les réécrit après : une écriture
        explicite sur un champ calculé le retire de la file de recalcul, ce
        qu'un simple `write` groupé ne garantit pas.
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
        string="Envoyer en tant que",
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
    @api.depends_context("uid")
    def _compute_bf_identity_allowed_ids(self):
        usable = self.env["bf.email.identity"]._usable_for(self.env.user)
        count = len(usable)
        for composer in self:
            composer.bf_identity_allowed_ids = usable
            composer.bf_identity_count = count

    @api.onchange("bf_identity_id")
    def _onchange_bf_identity_id(self):
        """Suivre l'identité choisie : le « De ».

        Il n'y a plus de signature à échanger dans le corps — elle n'y entre
        jamais, elle est posée à l'envoi et suit l'identité à ce moment-là
        (voir ``mail_thread._notify_by_email_prepare_rendering_context``).
        C'est ce qui a supprimé la substitution fragile qui vivait ici : elle
        devait retrouver dans un corps assaini le bloc exact qu'elle avait
        posé non assaini, et renonçait dès que la personne y avait touché.
        """
        if self.bf_identity_id:
            self.email_from = self.bf_identity_id.email_formatted

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
        self._bf_retarget_to_chatter()
        return super().action_schedule_message(scheduled_date=scheduled_date)

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
            return
        return super()._compute_partner_cc_bcc_ids()
