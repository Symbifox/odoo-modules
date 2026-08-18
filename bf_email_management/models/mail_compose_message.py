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

from odoo import _, api, fields, models


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

    def _action_send_mail(self, auto_commit=False):
        self._bf_retarget_to_chatter()
        return super()._action_send_mail(auto_commit=auto_commit)

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
