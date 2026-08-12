from odoo import api, fields, models


class PrivacyNotice(models.Model):
    _name = "privacy.notice"
    _description = "Modèle de consentement"
    _inherit = ["privacy.framework.mixin"]
    _order = "name"

    name = fields.Char(
        string="Nom",
        required=True,
        translate=True,
    )
    purpose_id = fields.Many2one(
        comodel_name="privacy.purpose",
        string="Objet",
        ondelete="restrict",
        index=True,
    )
    body_fr = fields.Html(
        string="Contenu (français)",
        sanitize_style=True,
        help="Texte du modèle de consentement en français",
    )
    body_en = fields.Html(
        string="Contenu (anglais)",
        sanitize_style=True,
        help="Texte du modèle de consentement en anglais",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )

    # Purpose fields exposed directly on the notice (fusion UI)
    purpose_code = fields.Char(
        related="purpose_id.code",
        readonly=False,
        string="Code",
    )
    plain_language_summary = fields.Html(
        related="purpose_id.plain_language_summary",
        readonly=False,
        string="Résumé en langage clair",
    )
    default_validity_days = fields.Integer(
        related="purpose_id.default_validity_days",
        readonly=False,
        string="Validité par défaut (jours)",
    )
    requires_consent = fields.Boolean(
        related="purpose_id.requires_consent",
        readonly=False,
    )
    requires_express_opt_in = fields.Boolean(
        related="purpose_id.requires_express_opt_in",
        readonly=False,
    )
    channel_scope = fields.Selection(
        related="purpose_id.channel_scope",
        readonly=False,
    )
    context_scope = fields.Selection(
        related="purpose_id.context_scope",
        readonly=False,
    )
    auto_expire_pending_days = fields.Integer(
        related="purpose_id.auto_expire_pending_days",
        readonly=False,
        string="Expiration auto. en attente (jours)",
    )

    # Related versions
    version_ids = fields.One2many(
        comodel_name="privacy.notice.version",
        inverse_name="notice_id",
        string="Versions",
    )
    version_count = fields.Integer(
        compute="_compute_version_count",
        string="Nombre de versions",
    )
    current_version_id = fields.Many2one(
        comodel_name="privacy.notice.version",
        compute="_compute_current_version",
        string="Version courante",
    )

    @api.depends("version_ids")
    def _compute_version_count(self):
        for record in self:
            record.version_count = len(record.version_ids)

    @api.depends("version_ids", "version_ids.effective_date")
    def _compute_current_version(self):
        today = fields.Date.today()
        for record in self:
            valid_versions = record.version_ids.filtered(
                lambda v: v.effective_date and v.effective_date <= today
            ).sorted(key=lambda v: v.effective_date, reverse=True)
            record.current_version_id = valid_versions[:1].id if valid_versions else False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("purpose_id"):
                # Auto-create a 1:1 purpose with the same name
                purpose = self.env["privacy.purpose"].create({
                    "name": vals.get("name", ""),
                    "code": vals.get("purpose_code", vals.get("name", "").lower().replace(" ", "_")[:30]),
                    "company_id": vals.get("company_id", self.env.company.id),
                })
                vals["purpose_id"] = purpose.id
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if "name" in vals:
            for record in self:
                if record.purpose_id:
                    record.purpose_id.name = vals["name"]
        return res

    def action_create_version(self):
        """Open wizard to create a new version of this notice."""
        self.ensure_one()
        # Get body based on user language or default to French
        lang = self.env.user.lang or "fr_CA"
        body = self.body_fr if lang.startswith("fr") else (self.body_en or self.body_fr)

        return {
            "type": "ir.actions.act_window",
            "name": "Créer une version",
            "res_model": "privacy.notice.version",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_notice_id": self.id,
                "default_body": body,
                "default_effective_date": fields.Date.today(),
            },
        }

    def action_view_versions(self):
        """View all versions of this notice."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Versions - {self.name}",
            "res_model": "privacy.notice.version",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("notice_id", "=", self.id)],
            "context": {"default_notice_id": self.id},
        }
