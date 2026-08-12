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

from odoo import api, models


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

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
