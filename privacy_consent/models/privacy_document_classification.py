from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class PrivacyDocumentClassification(models.Model):
    """Classification des renseignements personnels dans les documents.

    Tague n'importe quel enregistrement Odoo avec les catégories de RP
    qu'il contient, le niveau de sensibilité et la règle de conservation
    applicable. Utilise le pattern générique res_model/res_id.
    """

    _name = "privacy.document.classification"
    _description = "Classification documentaire (RP)"
    _order = "classified_at desc, id desc"
    _rec_name = "display_name"

    # Models that may contain personal information and can be classified.
    # System models (res.users, ir.*, etc.) are excluded to prevent
    # accidental destruction of Odoo infrastructure.
    #
    # Cette constante ne couvre que les modèles Odoo standards. Un module
    # maison n'a pas à être listé ici : il ajoute les siens en surchargeant
    # `_privacy_classifiable_models()`.
    ALLOWED_MODELS = {
        "res.partner",
        "project.project",
        "project.task",
        "hr.employee",
        "hr.contract",
        "account.move",
        "account.move.line",
        "sale.order",
        "purchase.order",
        "crm.lead",
        "helpdesk.ticket",
        "mail.message",
        "ir.attachment",
        "privacy.consent",
        "privacy.consent.evidence",
        "survey.user_input",
        "survey.user_input.line",
        "project.credential",
    }

    # Generic reference (like mail.activity)
    res_model = fields.Char(
        string="Modèle",
        required=True,
        index=True,
    )
    res_id = fields.Many2oneReference(
        string="Enregistrement",
        model_field="res_model",
        required=True,
        index=True,
    )
    res_name = fields.Char(
        string="Document",
        compute="_compute_res_name",
        store=True,
    )

    # PI classification
    pi_category = fields.Selection(
        selection=[
            ("identification", "Identification (nom, NAS, courriel)"),
            ("medical", "Médical / santé"),
            ("financial", "Financier"),
            ("biometric", "Biométrique"),
            ("geolocation", "Géolocalisation"),
            ("criminal", "Antécédents judiciaires"),
            ("opinion_political", "Opinions politiques / syndicales"),
            ("ethnic_racial", "Origine ethnique / raciale"),
            ("minor", "Renseignements sur un mineur"),
            ("other", "Autre"),
        ],
        string="Catégorie de RP",
        required=True,
        index=True,
    )
    sensitivity_level = fields.Selection(
        selection=[
            ("public", "Public"),
            ("internal", "Interne"),
            ("confidential", "Confidentiel"),
            ("highly_confidential", "Hautement confidentiel"),
        ],
        string="Niveau de sensibilité",
        required=True,
        default="confidential",
    )
    contains_direct_identifiers = fields.Boolean(
        string="Identifiants directs",
        help="Contient des identifiants directs (nom, NAS, courriel, etc.)",
    )
    contains_indirect_identifiers = fields.Boolean(
        string="Identifiants indirects",
        help="Contient des identifiants indirects (âge, code postal, etc.)",
    )

    # Links
    subject_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Sujet des données",
        index=True,
        help="La personne dont les renseignements personnels sont contenus dans le document",
    )
    retention_calendar_id = fields.Many2one(
        comodel_name="privacy.retention.calendar",
        string="Règle de conservation",
        index=True,
        ondelete="restrict",
        help="Règle du calendrier de conservation applicable à ce document",
    )
    document_date = fields.Date(
        string="Date du document",
        index=True,
        help=(
            "Date à partir de laquelle la période de rétention est calculée "
            "(création du document, signature, dernière utilisation). "
            "À défaut, utilise la date de classification."
        ),
    )
    retention_expiry_date = fields.Date(
        string="Fin de rétention",
        compute="_compute_retention_expiry_date",
        store=True,
        index=True,
        help="Date à laquelle la période de conservation expire",
    )

    # Audit
    classified_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Classifié par",
        default=lambda self: self.env.user,
    )
    classified_at = fields.Datetime(
        string="Classifié le",
        default=fields.Datetime.now,
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string="Notes")

    _sql_constraints = [
        (
            "record_category_company_uniq",
            "unique(res_model, res_id, pi_category, company_id)",
            "Un document ne peut être classifié qu'une fois par catégorie de RP !",
        ),
    ]

    def _privacy_classifiable_models(self):
        """Modèles qu'on accepte de classifier.

        Point d'extension : un pont vie privée ajoute les modèles de son
        propre module en surchargeant cette méthode, plutôt qu'en rouvrant
        `ALLOWED_MODELS`. La surcharge compose, donc plusieurs ponts
        peuvent cohabiter ::

            def _privacy_classifiable_models(self):
                return super()._privacy_classifiable_models() | {
                    "secure.transfer", "secure.transfer.file",
                }

        Redéfinir `ALLOWED_MODELS` fonctionne aussi, mais remplace la liste
        au lieu de s'y ajouter : le dernier module chargé gagne, et les
        autres perdent leurs modèles sans le dire.
        """
        return set(self.ALLOWED_MODELS)

    @api.constrains("res_model")
    def _check_allowed_model(self):
        """Restrict classification to models that may contain personal data.

        Prevents classification (and therefore potential destruction) of
        system-critical models like res.users, ir.module.module, etc.
        """
        allowed = self._privacy_classifiable_models()
        for record in self:
            if record.res_model and record.res_model not in allowed:
                raise ValidationError(
                    f"Le modèle « {record.res_model} » ne peut pas être classifié. "
                    f"Seuls les modèles pouvant contenir des renseignements personnels "
                    f"sont autorisés."
                )

    @api.depends("res_model", "res_id")
    def _compute_res_name(self):
        for record in self:
            if record.res_model and record.res_id:
                try:
                    target = self.env[record.res_model].browse(record.res_id)
                    if target.exists():
                        record.res_name = target.display_name
                    else:
                        record.res_name = f"{record.res_model},{record.res_id} [supprimé]"
                except Exception:
                    record.res_name = f"{record.res_model},{record.res_id}"
            else:
                record.res_name = False

    def _compute_display_name(self):
        category_labels = dict(
            self._fields["pi_category"]._description_selection(self.env)
        )
        for record in self:
            cat = category_labels.get(record.pi_category, record.pi_category)
            doc = record.res_name or f"{record.res_model},{record.res_id}"
            record.display_name = f"{doc} — {cat}"

    @api.depends("retention_calendar_id", "retention_calendar_id.total_retention_days",
                 "document_date", "classified_at")
    def _compute_retention_expiry_date(self):
        """Compute expiry from document_date (preferred) or classified_at.

        The retention period runs from the document's own date (creation,
        signature, last use), not from when it happened to be classified,
        to comply with Art. 23 LPRPSP.
        """
        for record in self:
            if not record.retention_calendar_id:
                record.retention_expiry_date = False
                continue
            base = record.document_date
            if not base and record.classified_at:
                base = record.classified_at.date()
            if not base:
                record.retention_expiry_date = False
                continue
            days = record.retention_calendar_id.total_retention_days
            record.retention_expiry_date = base + timedelta(days=days)

    def action_view_source_record(self):
        """Open the classified record in form view."""
        self.ensure_one()
        if not self.res_model or not self.res_id:
            raise UserError("Aucun enregistrement source associé.")
        if self.res_model not in self.env:
            raise UserError(
                f"Modèle « {self.res_model} » introuvable."
            )
        target = self.env[self.res_model].browse(self.res_id).exists()
        if not target:
            raise UserError("L'enregistrement source n'existe plus.")
        target.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "views": [[False, "form"]],
            "target": "current",
        }
