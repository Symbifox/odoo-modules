import ipaddress
import logging
from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from . import _fernet

_logger = logging.getLogger(__name__)

# Per-provider default API endpoint. openai_compatible has no default — the
# admin must set base_url (e.g. http://localhost:11434/v1 for Ollama).
_DEFAULT_BASE_URL = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
}

_DEFAULT_MODEL = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
}

# Feature → per-feature model-field map (single source of truth). Blank field
# falls back to the base `model`. extract and vision deliberately share
# model_vision.
_FEATURE_FIELD = {
    "chat": "model_chat",
    "vision": "model_vision",
    "extract": "model_vision",
    "title": "model_title",
    "triage": "model_triage",
    "ocr": "model_ocr",
}


class BfLlmProvider(models.Model):
    _name = "bf.llm.provider"
    _description = "LLM Provider Configuration"
    _order = "is_default desc, sequence, id"

    name = fields.Char(required=True, help="Display label, e.g. 'Anthropic (prod)'.")
    sequence = fields.Integer(default=10, help="Tie-break ordering.")
    provider = fields.Selection(
        [
            ("anthropic", "Anthropic"),
            ("openai", "OpenAI"),
            ("openai_compatible", "OpenAI-compatible / local"),
        ],
        required=True,
        default="anthropic",
    )
    base_url = fields.Char(
        groups="bf_llm.group_bf_llm_manager",
        help="API base URL. Blank uses the per-provider default "
        "(Anthropic: https://api.anthropic.com, OpenAI: https://api.openai.com/v1). "
        "Required for OpenAI-compatible/local (e.g. http://localhost:11434/v1).",
    )
    api_key = fields.Char(
        string="API Key",
        compute="_compute_api_key",
        inverse="_inverse_api_key",
        groups="bf_llm.group_bf_llm_manager",
        help="Stored Fernet-encrypted in ir.config_parameter — never as a column. "
        "May be blank for OpenAI-compatible/local servers that need no key.",
    )
    model = fields.Char(
        required=True,
        default="claude-opus-4-8",
        help="Base/default model id — the fallback for every feature whose "
        "override is empty.",
    )
    model_chat = fields.Char(string="Model (chat)", help="Override for chat. Blank → base model.")
    model_vision = fields.Char(
        string="Model (vision/extract)",
        help="Override for vision/extract. Blank → base model.",
    )
    model_title = fields.Char(string="Model (title)", help="Override for short title generation. Blank → base model.")
    model_triage = fields.Char(string="Model (triage)", help="Override for helpdesk triage. Blank → base model.")
    model_ocr = fields.Char(string="Model (OCR)", help="Override for invoice OCR. Blank → base model.")
    enabled = fields.Boolean(default=True, help="A disabled provider is never returned by default()/for_feature().")
    timeout = fields.Integer(default=60, help="Request timeout in seconds.")
    max_tokens = fields.Integer(default=4096, help="Per-call max_tokens ceiling unless the caller passes one.")
    supports_vision = fields.Boolean(
        default=True,
        help="Capability flag — drives the extract() degradation path "
        "(Tesseract text pre-pass when disabled).",
    )
    supports_tools = fields.Boolean(
        default=True,
        help="Capability flag — drives chat(tools=…) degradation.",
    )
    is_default = fields.Boolean(
        default=False,
        help="At most one provider may be the default. Setting this unsets the others.",
    )

    # ------------------------------------------------------------------
    # Endpoint validation (anti-SSRF / no-cleartext-key)
    # ------------------------------------------------------------------

    @api.constrains("base_url", "provider")
    def _check_base_url(self):
        for rec in self:
            if not rec.base_url:
                continue
            parsed = urlparse((rec.base_url or "").strip())
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValidationError(_("The base URL must be a valid http(s) URL."))
            # Cloud providers (Anthropic/OpenAI) carry a real API key: force TLS
            # and forbid internal/loopback targets so the key cannot be exfiltrated
            # to an attacker-controlled or internal host (SSRF). Local/compatible
            # providers (Ollama etc.) legitimately use http://localhost.
            if rec.provider != "openai_compatible":
                if parsed.scheme != "https":
                    raise ValidationError(_(
                        "Anthropic/OpenAI providers require an https:// base URL "
                        "(the API key must never travel in cleartext)."))
                try:
                    addr = ipaddress.ip_address(parsed.hostname)
                    if (addr.is_private or addr.is_loopback
                            or addr.is_link_local or addr.is_reserved):
                        raise ValidationError(_(
                            "An internal/private address is not allowed for a "
                            "cloud provider base URL."))
                except ValueError:
                    pass  # hostname (not a literal IP) — fine

    # ------------------------------------------------------------------
    # API key (Fernet-encrypted, per-provider ir.config_parameter)
    # ------------------------------------------------------------------

    def _api_key_param(self):
        self.ensure_one()
        return "bf_llm.provider.%s.api_key_encrypted" % self.id

    def _compute_api_key(self):
        Param = self.env["ir.config_parameter"].sudo()
        for rec in self:
            if isinstance(rec.id, int) and rec.id:
                encrypted = Param.get_param(rec._api_key_param(), "")
                rec.api_key = _fernet.decrypt(encrypted)
            else:
                # NewId (unsaved form/onchange record) — nothing persisted yet.
                rec.api_key = ""

    def _inverse_api_key(self):
        Param = self.env["ir.config_parameter"].sudo()
        for rec in self:
            if not (isinstance(rec.id, int) and rec.id):
                continue
            encrypted = _fernet.encrypt(rec.api_key or "")
            Param.set_param(rec._api_key_param(), encrypted)

    def _decrypt_key(self):
        """Return the decrypted API key, or "" (fail-closed)."""
        self.ensure_one()
        Param = self.env["ir.config_parameter"].sudo()
        return _fernet.decrypt(Param.get_param(self._api_key_param(), ""))

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _model_for(self, feature):
        """Resolve the model id for a feature, falling back to the base model."""
        self.ensure_one()
        field = _FEATURE_FIELD.get(feature)
        override = getattr(self, field) if field else False
        return override or self.model

    def _effective_base_url(self):
        """Resolve the base URL, applying the per-provider default if blank."""
        self.ensure_one()
        base = self.base_url or _DEFAULT_BASE_URL.get(self.provider)
        if not base:
            raise UserError(
                _(
                    "Aucune URL de base n'est configuree pour le fournisseur LLM "
                    "« %s » (requis pour OpenAI-compatible/local)."
                )
                % self.name
            )
        return base

    # ------------------------------------------------------------------
    # Onchange + single-default guard
    # ------------------------------------------------------------------

    @api.onchange("provider")
    def _onchange_provider(self):
        if not self.provider:
            return
        self.base_url = _DEFAULT_BASE_URL.get(self.provider) or False
        if not self.model or self.model in _DEFAULT_MODEL.values():
            self.model = _DEFAULT_MODEL.get(self.provider) or False

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._enforce_single_default()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get("is_default"):
            self._enforce_single_default()
        return res

    def _enforce_single_default(self):
        """Keep at most one is_default=True row. CE-safe write-time guard."""
        flagged = self.filtered("is_default")
        if not flagged:
            return
        keep = flagged[0]
        others = self.sudo().search(
            [("is_default", "=", True), ("id", "!=", keep.id)]
        )
        if others:
            others.write({"is_default": False})
