from odoo import api, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    @api.model
    def _load_pos_data(self, data):
        """A host logs in with a PIN like any employee: marked, the register shows the floor plan only."""
        result = super()._load_pos_data(data)
        config = self.env["pos.config"].browse(data["pos.config"]["data"][0]["id"])
        hosts = set(config.sudo().bf_host_employee_ids.ids) if config.bf_host else set()
        for employee in result["data"]:
            employee["_bf_host"] = employee["id"] in hosts and employee.get("_role") != "manager"
        return result
