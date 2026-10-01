import json

from odoo import _, fields, models
from odoo.exceptions import UserError


class BfBiDashboardVersion(models.Model):
    _name = "bf.bi.dashboard.version"
    _description = "Dashboard version"
    _order = "revision desc"

    dashboard_id = fields.Many2one(
        "spreadsheet.dashboard", string="Dashboard", required=True, ondelete="cascade", index=True)
    revision = fields.Integer(string="Revision", readonly=True)
    spreadsheet_data = fields.Text(string="Content", readonly=True)

    def _compute_display_name(self):
        for version in self:
            version.display_name = _("%(name)s, revision %(rev)s",
                                     name=version.dashboard_id.name, rev=version.revision)

    def action_restore(self):
        """Remet cette version en service, par-dessus la révision courante."""
        self.ensure_one()
        dashboard = self.dashboard_id
        resultat = dashboard.bf_save(json.loads(self.spreadsheet_data), dashboard.bf_revision)
        if resultat["status"] != "saved":
            raise UserError(_("Someone saved this dashboard meanwhile. Reload it and try again."))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Revision %(rev)s restored (new revision %(new)s).",
                             rev=self.revision, new=resultat["revision"]),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
