from odoo import api, fields, models


class PrivacyRetentionPolicy(models.Model):
    """Politique de rétention des données par finalité."""

    _name = "privacy.retention.policy"
    _description = "Politique de rétention des données"
    _order = "purpose_id, id"

    purpose_id = fields.Many2one(
        comodel_name="privacy.purpose",
        string="Finalité",
        required=True,
        ondelete="cascade",
        index=True,
        help="La finalité à laquelle cette politique de rétention s'applique",
    )
    retention_days = fields.Integer(
        string="Jours de rétention",
        required=True,
        default=365,
        help="Nombre de jours de conservation des données après l'événement déclencheur",
    )
    trigger_on = fields.Selection(
        selection=[
            ("expiration", "Après expiration"),
            ("withdrawal", "Après révocation"),
            ("both", "Après expiration ou révocation"),
        ],
        string="Déclencheur",
        default="both",
        required=True,
        help="Quand commencer à compter la période de rétention",
    )
    destruction_method = fields.Selection(
        selection=[
            ("anonymize", "Anonymiser les données"),
            ("delete", "Supprimer les enregistrements"),
            ("archive", "Archiver seulement"),
            ("manual", "Révision manuelle"),
        ],
        string="Méthode de destruction",
        default="anonymize",
        required=True,
        help="Comment traiter les données après la période de rétention",
    )
    data_categories = fields.Text(
        string="Catégories de données",
        help="Catégories de données personnelles couvertes par cette politique",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )
    notes = fields.Text(
        string="Notes",
        help="Notes supplémentaires concernant cette politique de rétention",
    )

    # Statistics
    destruction_request_count = fields.Integer(
        string="Demandes de destruction",
        compute="_compute_destruction_request_count",
    )

    _sql_constraints = [
        (
            "purpose_company_uniq",
            "unique(purpose_id, company_id)",
            "Chaque finalité ne peut avoir qu'une seule politique de rétention par société !",
        ),
    ]

    @api.depends("purpose_id")
    def _compute_display_name(self):
        for record in self:
            if record.purpose_id:
                record.display_name = f"Rétention : {record.purpose_id.name}"
            else:
                record.display_name = "Nouvelle politique de rétention"

    def _compute_destruction_request_count(self):
        for record in self:
            record.destruction_request_count = self.env["privacy.destruction.request"].search_count([
                ("policy_id", "=", record.id),
            ])

    def action_view_destruction_requests(self):
        """View destruction requests for this policy."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Demandes de destruction - {self.purpose_id.name}",
            "res_model": "privacy.destruction.request",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("policy_id", "=", self.id)],
            "context": {"default_policy_id": self.id},
        }
