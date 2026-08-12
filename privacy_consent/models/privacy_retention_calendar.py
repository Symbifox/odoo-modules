from datetime import timedelta

from odoo import api, fields, models


class PrivacyRetentionCalendar(models.Model):
    """Calendrier de conservation documentaire (Art. 3.2 LPRPSP).

    Définit les durées de conservation par type de document,
    la base légale et la disposition finale (destruction, anonymisation,
    conservation permanente ou transfert).
    """

    _name = "privacy.retention.calendar"
    _description = "Calendrier de conservation"
    _order = "code, id"
    _inherit = ["mail.thread", "privacy.framework.mixin"]

    name = fields.Char(
        string="Nom de la règle",
        required=True,
        tracking=True,
    )
    code = fields.Char(
        string="Code",
        required=True,
        index=True,
        tracking=True,
        help="Code unique (ex: FIN-001, RH-003)",
    )
    document_type = fields.Selection(
        selection=[
            ("contract", "Contrat"),
            ("invoice", "Facture"),
            ("hr_file", "Dossier employé"),
            ("project_file", "Dossier de projet"),
            ("meeting_record", "Compte rendu de rencontre"),
            ("consent_record", "Enregistrement de consentement"),
            ("credential", "Identifiant / mot de passe"),
            ("medical", "Document médical"),
            ("legal", "Document juridique"),
            ("tax", "Document fiscal"),
            ("marketing", "Document marketing"),
            ("correspondence", "Correspondance"),
            ("other", "Autre"),
        ],
        string="Type de document",
        required=True,
        tracking=True,
    )
    description = fields.Text(
        string="Description",
        help="Documents couverts par cette règle de conservation",
    )
    legal_basis = fields.Text(
        string="Base légale",
        required=True,
        help="Référence législative justifiant la durée de conservation",
    )

    # Retention periods
    active_retention_years = fields.Integer(
        string="Conservation active (années)",
        required=True,
        default=3,
        help="Durée de conservation en utilisation courante",
    )
    semi_active_retention_years = fields.Integer(
        string="Conservation semi-active (années)",
        default=0,
        help="Durée de conservation en entreposage (après la période active)",
    )
    total_retention_days = fields.Integer(
        string="Rétention totale (jours)",
        compute="_compute_total_retention_days",
        store=True,
        help="Durée totale de conservation en jours",
    )

    # Final disposition
    final_disposition = fields.Selection(
        selection=[
            ("destroy", "Destruction"),
            ("anonymize", "Anonymisation"),
            ("permanent_archive", "Conservation permanente"),
            ("transfer", "Transfert"),
        ],
        string="Sort final",
        required=True,
        default="destroy",
        tracking=True,
    )
    destruction_method = fields.Selection(
        selection=[
            ("anonymize", "Anonymiser les données"),
            ("delete", "Supprimer les enregistrements"),
            ("secure_wipe", "Effacement sécurisé"),
            ("manual", "Révision manuelle"),
        ],
        string="Méthode de destruction",
        default="delete",
        help="Méthode appliquée lors de la destruction",
    )
    requires_approval = fields.Boolean(
        string="Approbation requise",
        default=True,
        help="Nécessite l'approbation du responsable de la vie privée avant destruction",
    )

    # Scope
    applies_to_model = fields.Char(
        string="Modèle Odoo cible",
        help="Modèle technique Odoo auquel cette règle s'applique (ex: ir.attachment)",
    )

    # Review
    last_review_date = fields.Date(
        string="Dernière révision",
        tracking=True,
    )
    next_review_date = fields.Date(
        string="Prochaine révision",
        compute="_compute_next_review_date",
        store=True,
    )
    reviewed_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Révisé par",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string="Notes")

    # Statistics
    classification_count = fields.Integer(
        string="Documents classifiés",
        compute="_compute_classification_count",
    )

    _sql_constraints = [
        (
            "code_company_uniq",
            "unique(code, company_id)",
            "Le code doit être unique par société !",
        ),
    ]

    @api.depends("active_retention_years", "semi_active_retention_years")
    def _compute_total_retention_days(self):
        for record in self:
            total_years = (
                record.active_retention_years + record.semi_active_retention_years
            )
            record.total_retention_days = total_years * 365

    @api.depends("last_review_date")
    def _compute_next_review_date(self):
        for record in self:
            if record.last_review_date:
                record.next_review_date = record.last_review_date + timedelta(days=365)
            else:
                record.next_review_date = False

    def _compute_classification_count(self):
        Classification = self.env["privacy.document.classification"]
        for record in self:
            record.classification_count = Classification.search_count([
                ("retention_calendar_id", "=", record.id),
            ])

    def _compute_display_name(self):
        for record in self:
            record.display_name = f"[{record.code}] {record.name}" if record.code else record.name

    def action_view_classifications(self):
        """View documents classified under this calendar rule."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Documents — {self.name}",
            "res_model": "privacy.document.classification",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("retention_calendar_id", "=", self.id)],
            "context": {"default_retention_calendar_id": self.id},
        }

    def action_create_campaign(self):
        """Create a destruction campaign from this calendar rule."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Nouvelle campagne de destruction",
            "res_model": "privacy.destruction.campaign",
            "views": [[False, "form"]],
            "target": "current",
            "context": {
                "default_retention_calendar_id": self.id,
                "default_name": f"Campagne — {self.name}",
                "default_cutoff_date": fields.Date.today(),
            },
        }
