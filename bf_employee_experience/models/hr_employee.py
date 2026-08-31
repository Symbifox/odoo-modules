from odoo import _, fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    ex_entitlement_ids = fields.One2many(
        "bf.ex.entitlement", "employee_id", string="Droits aux avantages",
    )
    ex_entitlement_count = fields.Integer(
        string="Avantages", compute="_compute_ex_counts",
    )
    ex_usage_count = fields.Integer(
        string="Usages", compute="_compute_ex_counts",
    )

    def _compute_ex_counts(self):
        today = fields.Date.context_today(self)
        Entitlement = self.env["bf.ex.entitlement"]
        Usage = self.env["bf.ex.usage"]
        for employee in self:
            employee.ex_entitlement_count = Entitlement.search_count([
                ("employee_id", "=", employee.id),
                ("date_start", "<=", today),
                "|", ("date_end", "=", False), ("date_end", ">=", today),
            ])
            employee.ex_usage_count = Usage.search_count([
                ("employee_id", "=", employee.id),
            ])

    def action_view_ex_entitlements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Droits aux avantages"),
            "res_model": "bf.ex.entitlement",
            "view_mode": "list,form",
            "domain": [("employee_id", "=", self.id)],
            "context": {"default_employee_id": self.id},
        }
