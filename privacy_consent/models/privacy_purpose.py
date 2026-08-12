from odoo import api, fields, models


class PrivacyPurpose(models.Model):
    _name = "privacy.purpose"
    _description = "Objet de consentement"
    _inherit = ["privacy.framework.mixin"]
    _order = "sequence, name"

    code = fields.Char(
        string="Code",
        required=True,
        index=True,
        help="Code unique pour cette finalité (ex. : « marketing », « enregistrement »)",
    )
    name = fields.Char(
        string="Nom",
        required=True,
        translate=True,
    )
    plain_language_summary = fields.Html(
        string="Résumé en langage clair",
        translate=True,
        sanitize_style=True,
        help="Explication claire de la finalité en langage simple (exigence Loi 25)",
    )
    requires_consent = fields.Boolean(
        string="Consentement requis",
        default=True,
        help="Si coché, un consentement explicite est requis pour cette finalité",
    )
    requires_express_opt_in = fields.Boolean(
        string="Consentement explicite requis",
        default=False,
        help="Si coché, un consentement affirmatif explicite est requis (non implicite)",
    )
    default_validity_days = fields.Integer(
        string="Validité par défaut (jours)",
        default=365,
        help="Nombre de jours pendant lesquels le consentement reste valide (0 = sans expiration)",
    )
    channel_scope = fields.Selection(
        selection=[
            ("email", "Courriel"),
            ("sms", "SMS"),
            ("phone", "Téléphone"),
            ("video", "Vidéo"),
            ("in_person", "En personne"),
            ("all", "Tous les canaux"),
        ],
        string="Portée du canal",
        default="all",
        help="Canal de communication auquel cette finalité s'applique",
    )
    context_scope = fields.Selection(
        selection=[
            ("project", "Projet"),
            ("marketing", "Marketing"),
            ("meeting", "Réunion"),
            ("crm", "Ventes/CRM"),
            ("general", "Général"),
        ],
        string="Portée du contexte",
        default="general",
        help="Contexte dans lequel cette finalité est habituellement utilisée",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )

    # Auto-expiration for pending consents
    auto_expire_pending_days = fields.Integer(
        string="Expiration auto. en attente (jours)",
        default=0,
        help="Expirer automatiquement les demandes de consentement en attente après ce nombre de jours (0 = pas d'expiration automatique)",
    )

    # Related fields
    notice_ids = fields.One2many(
        comodel_name="privacy.notice",
        inverse_name="purpose_id",
        string="Modèles de consentement",
    )
    consent_ids = fields.One2many(
        comodel_name="privacy.consent",
        inverse_name="purpose_id",
        string="Consentements",
    )
    email_sequence_ids = fields.One2many(
        comodel_name="privacy.email.sequence",
        inverse_name="purpose_id",
        string="Séquences de courriel",
    )
    retention_policy_ids = fields.One2many(
        comodel_name="privacy.retention.policy",
        inverse_name="purpose_id",
        string="Politiques de rétention",
    )
    docuseal_template_ids = fields.One2many(
        comodel_name="privacy.docuseal.template",
        inverse_name="purpose_id",
        string="Modèles DocuSeal",
    )

    _sql_constraints = [
        (
            "code_company_uniq",
            "unique(code, company_id)",
            "Le code de finalité doit être unique par société !",
        ),
    ]

    @api.depends("code", "name")
    def _compute_display_name(self):
        for record in self:
            if record.code:
                record.display_name = f"[{record.code}] {record.name}"
            else:
                record.display_name = record.name or ""
