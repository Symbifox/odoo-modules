from odoo import api, fields, models


class AuditAssessment(models.Model):
    _name = "audit.assessment"
    _description = "IT audit assessment"
    _order = "client_id, element_id, supplier_id"

    client_id = fields.Many2one(
        "audit.client", string="Client", required=True, ondelete="cascade"
    )
    supplier_id = fields.Many2one(
        "audit.supplier", string="Supplier", required=True, ondelete="cascade"
    )
    element_id = fields.Many2one(
        "audit.element", string="Element", required=True, ondelete="cascade"
    )
    element_num = fields.Integer(
        related="element_id.num", string="No.", store=True
    )
    element_best_practices = fields.Html(
        related="element_id.best_practices", string="Best practices"
    )
    response = fields.Text(string="Supplier response")
    response_received_date = fields.Date(string="Response received on")
    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("to_validate", "To validate"),
            ("adequate", "Adequate"),
            ("partial", "Partial"),
            ("inadequate", "Inadequate"),
            ("declared", "Declared"),
            ("na", "N/A"),
        ],
        string="Status",
        default="pending",
    )
    notes = fields.Text(string="Assessor notes")
    recommendation = fields.Text(string="Recommendation to the client")
    assessed_by = fields.Many2one("res.users", string="Assessed by")
    assessed_date = fields.Date(string="Assessment date")

    _sql_constraints = [
        (
            "client_supplier_element_unique",
            "unique(client_id, supplier_id, element_id)",
            "This client/supplier/element combination already exists.",
        ),
    ]

    def write(self, vals):
        # Auto-set response_received_date when response goes from empty to filled
        if "response" in vals and vals["response"]:
            for rec in self:
                if not rec.response and not rec.response_received_date:
                    vals.setdefault("response_received_date", fields.Date.today())
                    break
        return super().write(vals)

    def action_create_watchpoint(self):
        """Create a watchpoint from this assessment."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Create a watchpoint"),
            "res_model": "audit.watchpoint",
            "views": [[False, "form"]],
            "target": "new",
            "context": {
                "default_client_id": self.client_id.id,
                "default_element_id": self.element_id.id,
                "default_source": "supplier_response",
                "default_description": self.env._(
                    "[%(supplier)s] Element %(num)s: %(name)s",
                    supplier=self.supplier_id.name,
                    num=self.element_id.num,
                    name=self.element_id.name,
                ),
            },
        }
