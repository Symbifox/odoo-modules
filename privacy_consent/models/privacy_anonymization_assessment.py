import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrivacyAnonymizationAssessment(models.Model):
    """Évaluation d'anonymisation (Règlement A-2.1, r. 0.1).

    Workflow d'évaluation des 3 critères du Règlement sur l'anonymisation
    des renseignements personnels (mai 2024) :
    1. Individualisation — impossibilité d'isoler une personne
    2. Corrélation — impossibilité de relier des jeux de données
    3. Inférence — impossibilité de déduire des RP
    """

    _name = "privacy.anonymization.assessment"
    _description = "Évaluation d'anonymisation"
    _inherit = ["mail.thread", "mail.activity.mixin", "privacy.framework.mixin"]
    _order = "create_date desc, id desc"

    RISK_LEVELS = [
        ("low", "Faible"),
        ("medium", "Moyen"),
        ("high", "Élevé"),
    ]

    name = fields.Char(
        string="Nom",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ("draft", "Brouillon"),
            ("analysis", "Analyse en cours"),
            ("completed", "Complétée"),
            ("reassessment_due", "Réévaluation requise"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    dataset_description = fields.Text(
        string="Description du jeu de données",
        required=True,
        help="Description complète des données à anonymiser",
    )

    # === Step 1: Direct identifier removal ===
    direct_identifiers_removed = fields.Boolean(
        string="Identifiants directs retirés",
        help="Les identifiants directs ont été retirés du jeu de données",
    )
    identifiers_list = fields.Text(
        string="Liste des identifiants retirés",
        help="Nom, NAS, courriel, téléphone, adresse, etc.",
    )
    removal_technique = fields.Selection(
        selection=[
            ("deletion", "Suppression"),
            ("masking", "Masquage"),
            ("pseudonymization", "Pseudonymisation"),
            ("generalization", "Généralisation"),
        ],
        string="Technique de retrait",
    )

    # === Step 2: Individualization criterion ===
    individualization_risk = fields.Selection(
        selection=RISK_LEVELS,
        string="Risque d'individualisation",
        help="Peut-on isoler ou distinguer une personne dans le jeu de données?",
    )
    individualization_analysis = fields.Text(
        string="Analyse d'individualisation",
        help="Évaluation détaillée du risque d'individualisation",
    )
    individualization_mitigations = fields.Text(
        string="Mesures — individualisation",
        help="Techniques appliquées pour prévenir l'individualisation",
    )

    # === Step 3: Correlation criterion ===
    correlation_risk = fields.Selection(
        selection=RISK_LEVELS,
        string="Risque de corrélation",
        help="Peut-on relier des jeux de données concernant la même personne?",
    )
    correlation_analysis = fields.Text(
        string="Analyse de corrélation",
        help="Évaluation détaillée du risque de corrélation",
    )
    correlation_mitigations = fields.Text(
        string="Mesures — corrélation",
        help="Techniques appliquées pour prévenir la corrélation",
    )

    # === Step 4: Inference criterion ===
    inference_risk = fields.Selection(
        selection=RISK_LEVELS,
        string="Risque d'inférence",
        help="Peut-on déduire des renseignements personnels à partir d'autres données?",
    )
    inference_analysis = fields.Text(
        string="Analyse d'inférence",
        help="Évaluation détaillée du risque d'inférence",
    )
    inference_mitigations = fields.Text(
        string="Mesures — inférence",
        help="Techniques appliquées pour prévenir l'inférence",
    )

    # === Overall result ===
    overall_risk = fields.Selection(
        selection=RISK_LEVELS,
        string="Risque global",
        compute="_compute_overall_risk",
        store=True,
        help="Niveau de risque le plus élevé des 3 critères",
    )
    is_effectively_anonymous = fields.Boolean(
        string="Effectivement anonyme",
        compute="_compute_overall_risk",
        store=True,
        help="Vrai uniquement si les 3 critères sont à risque faible",
    )
    techniques_used = fields.Text(
        string="Techniques utilisées",
        help="k-anonymity, l-diversity, differential privacy, bruit, etc.",
    )

    # === Assessment metadata ===
    assessed_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Évalué par",
        default=lambda self: self.env.user,
        tracking=True,
    )
    assessment_date = fields.Date(
        string="Date d'évaluation",
        tracking=True,
    )
    approved_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Approuvé par (responsable)",
        tracking=True,
        help="Le responsable de la protection des renseignements personnels "
        "(titre selon le cadre applicable : RPRP, DPO, Privacy Officer, etc.)",
    )
    approval_date = fields.Date(
        string="Date d'approbation",
        tracking=True,
    )

    # === Reassessment (Section 8 of Regulation) ===
    reassessment_interval_months = fields.Integer(
        string="Intervalle de réévaluation (mois)",
        default=12,
        help="Nombre de mois entre les réévaluations périodiques",
    )
    next_reassessment_date = fields.Date(
        string="Prochaine réévaluation",
        compute="_compute_next_reassessment_date",
        store=True,
    )
    parent_assessment_id = fields.Many2one(
        comodel_name="privacy.anonymization.assessment",
        string="Évaluation précédente",
        ondelete="restrict",
        help="Évaluation dont celle-ci est la réévaluation",
    )
    child_assessment_ids = fields.One2many(
        comodel_name="privacy.anonymization.assessment",
        inverse_name="parent_assessment_id",
        string="Réévaluations",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )

    # Valid state transitions (from → set of allowed to states)
    _STATE_TRANSITIONS = {
        "draft": {"analysis"},
        "analysis": {"completed"},
        "completed": {"reassessment_due"},
        "reassessment_due": {"analysis", "completed"},
    }

    def write(self, vals):
        """Enforce valid state transitions."""
        if "state" in vals and not self.env.context.get("skip_state_validation"):
            new_state = vals["state"]
            for record in self:
                allowed = self._STATE_TRANSITIONS.get(record.state, set())
                if new_state != record.state and new_state not in allowed:
                    raise UserError(
                        f"Transition d'état invalide : « {record.state} » → "
                        f"« {new_state} ». Utilisez les boutons d'action."
                    )
        return super().write(vals)

    @api.depends("individualization_risk", "correlation_risk", "inference_risk")
    def _compute_overall_risk(self):
        risk_order = {"high": 3, "medium": 2, "low": 1, False: 0}
        risk_reverse = {3: "high", 2: "medium", 1: "low", 0: False}
        for record in self:
            risks = [
                risk_order.get(record.individualization_risk, 0),
                risk_order.get(record.correlation_risk, 0),
                risk_order.get(record.inference_risk, 0),
            ]
            max_risk = max(risks)
            record.overall_risk = risk_reverse[max_risk]
            record.is_effectively_anonymous = (
                max_risk == 1
                and all(r == 1 for r in risks)
            )

    @api.depends("approval_date", "reassessment_interval_months")
    def _compute_next_reassessment_date(self):
        for record in self:
            if record.approval_date and record.reassessment_interval_months:
                record.next_reassessment_date = (
                    record.approval_date
                    + relativedelta(months=record.reassessment_interval_months)
                )
            else:
                record.next_reassessment_date = False

    def action_start_analysis(self):
        """Move to analysis state."""
        for record in self:
            if record.state != "draft":
                raise UserError("Seules les évaluations en brouillon peuvent être démarrées.")
            record.write({"state": "analysis"})
            record.message_post(
                body="Analyse d'anonymisation démarrée.",
                message_type="notification",
            )
        return True

    def action_complete(self):
        """Complete the analysis — validates all 3 criteria are filled."""
        for record in self:
            if record.state not in ("analysis", "reassessment_due"):
                raise UserError("L'évaluation doit être en analyse pour être complétée.")
            # Validate all 3 criteria
            missing = []
            if not record.individualization_risk:
                missing.append("individualisation")
            if not record.correlation_risk:
                missing.append("corrélation")
            if not record.inference_risk:
                missing.append("inférence")
            if missing:
                raise UserError(
                    "Les critères suivants doivent être évalués avant de compléter : "
                    + ", ".join(missing)
                )
            record.write({
                "state": "completed",
                "assessment_date": fields.Date.today(),
            })
            # Post summary
            record.message_post(
                body=(
                    f"Évaluation complétée. Risque global : {record.overall_risk}. "
                    f"Effectivement anonyme : {'Oui' if record.is_effectively_anonymous else 'Non'}."
                ),
                message_type="notification",
            )
        return True

    def action_approve(self):
        """Privacy Officer approval (Privacy Officer only)."""
        if not self.env.su and not self.env.user.has_group(
            "privacy_consent.group_privacy_officer"
        ):
            raise UserError(
                "Seul le responsable de la vie privée peut approuver "
                "une évaluation d'anonymisation."
            )
        for record in self:
            if record.state != "completed":
                raise UserError("Seules les évaluations complétées peuvent être approuvées.")
            record.write({
                "approved_by_id": self.env.user.id,
                "approval_date": fields.Date.today(),
            })
            record.message_post(
                body=f"Évaluation approuvée par {self.env.user.name}.",
                message_type="notification",
            )
        return True

    def action_create_reassessment(self):
        """Create a new assessment linked to this one for periodic reassessment."""
        self.ensure_one()
        new = self.copy({
            "name": f"Réévaluation — {self.name}",
            "state": "draft",
            "parent_assessment_id": self.id,
            "assessed_by_id": self.env.user.id,
            "assessment_date": False,
            "approved_by_id": False,
            "approval_date": False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "privacy.anonymization.assessment",
            "res_id": new.id,
            "views": [[False, "form"]],
            "target": "current",
        }

    @api.model
    def cron_check_reassessment_due(self):
        """Flag assessments past their reassessment date."""
        today = fields.Date.today()
        due = self.search([
            ("state", "=", "completed"),
            ("next_reassessment_date", "<=", today),
            ("next_reassessment_date", "!=", False),
        ])
        for assessment in due:
            # Only flag if no child reassessment already exists
            if not assessment.child_assessment_ids:
                assessment.write({"state": "reassessment_due"})
                assessment.activity_schedule(
                    "mail.mail_activity_data_todo",
                    note=(
                        f"La réévaluation d'anonymisation « {assessment.name} » "
                        f"est due depuis le {assessment.next_reassessment_date}."
                    ),
                    user_id=assessment.assessed_by_id.id or self.env.user.id,
                )
                _logger.info(
                    "Anonymization assessment %s flagged for reassessment", assessment.name
                )
