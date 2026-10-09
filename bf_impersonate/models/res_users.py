from odoo import _, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_impersonate_protected = fields.Boolean(
        string="Protected from impersonation",
        help="Nobody can see Symbifox as this account. Tick it for technical "
             "accounts (integrations, assistants) and anyone who must never be "
             "impersonated.",
    )

    def action_bf_impersonate(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("See Symbifox as %s", self.name),
            "res_model": "bf.impersonate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_target_user_id": self.id},
        }
