import logging
import os

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception
    _logger.warning(
        "cryptography package not installed. "
        "API key encryption will not be available for bf_claude_chat."
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    claude_enabled = fields.Boolean(
        string="Enable Claude AI",
        config_parameter="bf_claude_chat.enabled",
        default=True,
    )
    claude_streaming = fields.Boolean(
        string="Stream responses (live tokens)",
        config_parameter="bf_claude_chat.streaming",
        default=True,
        help="Stream the answer token by token with tool and thinking activity "
             "(console-like). When off, the chat falls back to a single "
             "buffered reply.",
    )
    claude_auto_brief = fields.Boolean(
        string="Brief me when I open a record",
        config_parameter="bf_claude_chat.auto_brief",
        default=True,
        help="The first time the panel opens on a record with no conversation "
             "yet, Claude reads the record, its chatter and its latest activity "
             "and opens with a short situation report plus next actions, "
             "without waiting for a question.",
    )
    claude_model = fields.Selection(
        [
            ("sonnet", "Sonnet (fast, recommended)"),
            ("opus", "Opus (most capable)"),
            ("haiku", "Haiku (fastest, lightweight)"),
        ],
        string="Claude Model",
        config_parameter="bf_claude_chat.model",
        default="sonnet",
    )
    claude_max_turns = fields.Integer(
        string="Max Turns per Message",
        config_parameter="bf_claude_chat.max_turns",
        default=45,
        help="Maximum number of tool-use cycles Claude can perform per message. "
             "Higher values allow more complex queries but take longer.",
    )
    claude_api_key = fields.Char(
        string="Anthropic API Key",
        compute="_compute_api_key",
        inverse="_inverse_api_key",
        help="Optional. If set, this tenant bills its own Anthropic key "
             "(pay-per-use). Leave empty to use the credentials the bridge "
             "already holds.",
    )
    claude_tenant = fields.Char(
        string="Tenant Slug",
        config_parameter="bf_claude_chat.tenant",
        default="pme",
        help="Tenant identifier sent to the bridge to select the correct "
             "MCP tools and system prompt (e.g. 'bf' or 'pme').",
    )
    claude_bridge_socket = fields.Char(
        string="Bridge Socket Path",
        config_parameter="bf_claude_chat.bridge_socket",
        default="/run/claude-bridge/bridge.sock",
        help="Unix socket path for the Claude bridge service.",
    )
    claude_timeout = fields.Integer(
        string="Response Timeout (seconds)",
        config_parameter="bf_claude_chat.timeout",
        default=660,
        help="Maximum time to wait for a Claude response.",
    )
    # --- Encryption helpers ---

    @staticmethod
    def _get_encryption_key():
        """Get Fernet key from env var or odoo.conf. Never from DB."""
        if not Fernet:
            return None
        # Priority 1: environment variable
        key = os.environ.get("BF_CLAUDE_CHAT_FERNET_KEY")
        if key:
            return key.encode()
        # Priority 2: odoo.conf
        try:
            from odoo.tools import config
            key = config.get("bf_claude_chat_fernet_key")
            if key:
                return key.encode()
        except Exception:
            pass
        _logger.warning(
            "bf_claude_chat: no Fernet key configured. "
            "Set BF_CLAUDE_CHAT_FERNET_KEY env var or "
            "bf_claude_chat_fernet_key in odoo.conf."
        )
        return None

    @staticmethod
    def _encrypt_api_key(value):
        if not value:
            return ""
        if not Fernet:
            raise UserError(
                "Le paquet 'cryptography' est requis pour stocker "
                "la cle API de maniere securisee."
            )
        key = ResConfigSettings._get_encryption_key()
        if not key:
            raise UserError(
                "Cle de chiffrement manquante. Configurez "
                "BF_CLAUDE_CHAT_FERNET_KEY dans l'environnement "
                "ou bf_claude_chat_fernet_key dans odoo.conf."
            )
        return Fernet(key).encrypt(value.encode()).decode()

    @staticmethod
    def _decrypt_api_key(env, encrypted):
        if not encrypted:
            return ""
        key = ResConfigSettings._get_encryption_key()
        if not key:
            _logger.warning("bf_claude_chat: cannot decrypt API key - no Fernet key")
            return ""
        try:
            return Fernet(key).decrypt(encrypted.encode()).decode()
        except InvalidToken:
            _logger.warning(
                "bf_claude_chat: API key decryption failed "
                "(key rotation or legacy plaintext value)"
            )
            return ""
        except Exception:
            _logger.exception("bf_claude_chat: API key decryption error")
            return ""

    # --- Computed field ---

    def _compute_api_key(self):
        encrypted = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_claude_chat.api_key_encrypted", "")
        )
        plaintext = self._decrypt_api_key(self.env, encrypted)
        for rec in self:
            rec.claude_api_key = plaintext

    def _inverse_api_key(self):
        for rec in self:
            encrypted = self._encrypt_api_key(rec.claude_api_key or "")
            self.env["ir.config_parameter"].sudo().set_param(
                "bf_claude_chat.api_key_encrypted", encrypted
            )
            # Clear any legacy plaintext key
            self.env["ir.config_parameter"].sudo().set_param(
                "bf_claude_chat.api_key", ""
            )

