import hashlib

from odoo import api, fields, models


class PrivacyNoticeVersion(models.Model):
    _name = "privacy.notice.version"
    _description = "Version de modèle de consentement"
    _order = "effective_date desc, version desc"
    _rec_name = "display_name"

    notice_id = fields.Many2one(
        comodel_name="privacy.notice",
        string="Modèle",
        required=True,
        ondelete="cascade",
        index=True,
    )
    version = fields.Char(
        string="Version",
        required=True,
        help="Numéro de version (ex. : « 1.0 », « 1.1 », « 2.0 »)",
    )
    effective_date = fields.Date(
        string="Date d'entrée en vigueur",
        required=True,
        default=fields.Date.today,
        help="Date à partir de laquelle cette version entre en vigueur",
    )
    body = fields.Html(
        string="Contenu",
        required=True,
        sanitize_style=True,
        help="Contenu exact affiché au moment du consentement (instantané immuable)",
    )
    hash = fields.Char(
        string="Empreinte du contenu",
        readonly=True,
        copy=False,
        help="Empreinte SHA256 du contenu pour la vérification d'intégrité",
    )

    # Metadata
    created_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Créé par",
        default=lambda self: self.env.user,
        readonly=True,
    )

    # Related consents using this version
    consent_ids = fields.One2many(
        comodel_name="privacy.consent",
        inverse_name="notice_version_id",
        string="Consentements",
    )
    consent_count = fields.Integer(
        compute="_compute_consent_count",
        string="Nombre de consentements",
    )

    _sql_constraints = [
        (
            "version_notice_uniq",
            "unique(notice_id, version)",
            "Le numéro de version doit être unique par modèle !",
        ),
    ]

    @api.depends("consent_ids")
    def _compute_consent_count(self):
        for record in self:
            record.consent_count = len(record.consent_ids)

    @api.depends("notice_id", "version")
    def _compute_display_name(self):
        for record in self:
            notice_name = record.notice_id.name or "Modèle"
            record.display_name = f"{notice_name} v{record.version}"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("body") and not vals.get("hash"):
                body_str = str(vals["body"]) if vals["body"] else ""
                vals["hash"] = hashlib.sha256(body_str.encode("utf-8")).hexdigest()
        return super().create(vals_list)

    def write(self, vals):
        # Prevent modification of body or hash after creation (immutable)
        if "body" in vals or "hash" in vals:
            # Check if any consents exist using these versions
            if any(rec.consent_count > 0 for rec in self):
                # Instead of raising, just remove body/hash from vals
                # to preserve immutability for versions with consents
                vals = {k: v for k, v in vals.items() if k not in ("body", "hash")}
        return super().write(vals)

    def action_view_consents(self):
        """View consents using this notice version."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Consentements - {self.display_name}",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("notice_version_id", "=", self.id)],
        }
