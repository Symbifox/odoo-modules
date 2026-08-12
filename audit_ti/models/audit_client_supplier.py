from odoo import api, fields, models

SUPPLIER_ELEMENT_COVERAGE = {
    "X": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
    "A": [2, 3, 4, 5, 6, 12, 13, 14],
    "C": [5, 7, 8, 9, 10, 12, 13, 14],
    "S": [5, 10, 12, 13, 14],
}


class AuditClientSupplier(models.Model):
    _name = "audit.client.supplier"
    _description = "Relation client-fournisseur audit"
    _order = "client_id, supplier_id"

    client_id = fields.Many2one(
        "audit.client", string="Client", required=True, ondelete="cascade"
    )
    supplier_id = fields.Many2one(
        "audit.supplier", string="Fournisseur", required=True, ondelete="cascade"
    )
    roles = fields.Char(
        string="Rôles",
        help="X=Prestataire TI, A=Application, C=Cloud, S=Sauvegarde",
    )

    _sql_constraints = [
        (
            "client_supplier_unique",
            "unique(client_id, supplier_id)",
            "Ce fournisseur est déjà associé à ce client.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._generate_assessments()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "roles" in vals:
            self._generate_assessments()
        return res

    def _generate_assessments(self):
        """Generate assessment records based on roles and element coverage."""
        Element = self.env["audit.element"]
        Assessment = self.env["audit.assessment"]
        all_elements = Element.search([])
        element_by_num = {e.num: e for e in all_elements}

        for rec in self:
            if not rec.roles:
                continue
            covered_nums = set()
            for role_char in rec.roles.upper():
                covered_nums.update(SUPPLIER_ELEMENT_COVERAGE.get(role_char, []))

            existing = Assessment.search([
                ("client_id", "=", rec.client_id.id),
                ("supplier_id", "=", rec.supplier_id.id),
            ])
            existing_elements = set(existing.mapped("element_id.num"))

            to_create = []
            for num in covered_nums:
                if num not in existing_elements and num in element_by_num:
                    to_create.append({
                        "client_id": rec.client_id.id,
                        "supplier_id": rec.supplier_id.id,
                        "element_id": element_by_num[num].id,
                        "status": "pending",
                    })
            if to_create:
                Assessment.create(to_create)
