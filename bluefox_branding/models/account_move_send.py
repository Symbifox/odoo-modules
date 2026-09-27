from odoo import api, models


class AccountMoveSend(models.AbstractModel):
    _inherit = 'account.move.send'

    @api.model
    def _get_mail_layout(self):
        """Odoo 18 sends invoices through account.move.send, not the composer: the
        composer map (mail_compose_message.py) never saw them, and every invoice email
        left with Odoo's stock layout, no tenant logo (found by a QA with real emails,
        2026-09-27)."""
        if self.env.ref('bluefox_branding.bf_mail_layout_with_signature', raise_if_not_found=False):
            return 'bluefox_branding.bf_mail_layout_with_signature'
        return super()._get_mail_layout()
