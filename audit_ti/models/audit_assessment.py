from odoo import api, fields, models


class AuditAssessment(models.Model):
    _name = "audit.assessment"
    _description = "Évaluation d'audit TI"
    _order = "client_id, element_id, supplier_id"

    client_id = fields.Many2one(
        "audit.client", string="Client", required=True, ondelete="cascade"
    )
    supplier_id = fields.Many2one(
        "audit.supplier", string="Fournisseur", required=True, ondelete="cascade"
    )
    element_id = fields.Many2one(
        "audit.element", string="Élément", required=True, ondelete="cascade"
    )
    element_num = fields.Integer(
        related="element_id.num", string="N°", store=True
    )
    element_best_practices = fields.Html(
        related="element_id.best_practices", string="Meilleures pratiques"
    )
    response = fields.Text(string="Réponse du fournisseur")
    response_received_date = fields.Date(string="Date réception réponse")
    status = fields.Selection(
        [
            ("pending", "En attente"),
            ("to_validate", "À valider"),
            ("adequate", "Adéquat"),
            ("partial", "Partiel"),
            ("inadequate", "Inadéquat"),
            ("declared", "Déclaré"),
            ("na", "N/A"),
        ],
        string="Statut",
        default="pending",
    )
    notes = fields.Text(string="Notes de l'évaluateur")
    recommendation = fields.Text(string="Recommandation au client")
    assessed_by = fields.Many2one("res.users", string="Évalué par")
    assessed_date = fields.Date(string="Date d'évaluation")

    _sql_constraints = [
        (
            "client_supplier_element_unique",
            "unique(client_id, supplier_id, element_id)",
            "Cette combinaison client/fournisseur/élément existe déjà.",
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
            "name": "Créer un point de vigilance",
            "res_model": "audit.watchpoint",
            "views": [[False, "form"]],
            "target": "new",
            "context": {
                "default_client_id": self.client_id.id,
                "default_element_id": self.element_id.id,
                "default_source": "supplier_response",
                "default_description": f"[{self.supplier_id.name}] Élément {self.element_id.num}: {self.element_id.name}",
            },
        }
