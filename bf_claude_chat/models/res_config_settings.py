import json
import logging
import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Ceiling on the personality text composed into the prompt. The bridge cuts at
# the same length; refusing here is what keeps that cut from being silent.
PERSONALITY_MAX_CHARS = 2000

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
    # Éteint d'office, locataire par locataire.
    claude_closure = fields.Boolean(
        string="Conversations aim to close",
        config_parameter="bf_claude_chat.closure_enabled",
        default=False,
        help="At the end of each turn Gen says where the conversation stands, "
             "and offers to archive it once everything is done. A nightly "
             "pass follows up on conversations left without news for two "
             "days. Nothing is ever archived without a click.",
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
        help="Optional. If set, this tenant uses its own API key (pay-per-use) "
             "instead of the shared Max plan. Leave empty to use the server's Max plan.",
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
        # Le paramètre appartient à bf_ai_bridge, qui porte le transport : ce
        # réglage est celui que TOUS les modules appelants lisent désormais.
        config_parameter="bf_ai_bridge.socket",
        default="/run/claude-bridge/bridge.sock",
        help="Unix socket path for the Claude bridge service. Shared by every "
             "Blue Fox module that talks to the bridge (meetings, invoice OCR, "
             "contact enrichment).",
    )
    claude_timeout = fields.Integer(
        string="Response Timeout (seconds)",
        config_parameter="bf_claude_chat.timeout",
        default=660,
        help="Maximum time to wait for a Claude response.",
    )
    # A Char, not a Text: res.config.settings only binds config_parameter to
    # char/boolean/integer/float/selection/many2one/datetime fields. The view
    # renders it as a textarea (widget="text"); a Char has no length limit.
    claude_personality = fields.Char(
        string="Gen's personality",
        config_parameter="bf_claude_chat.personality",
        help="How Gen speaks on this instance: the name it goes by, tu or vous, "
             "the default language, how long its answers run, how it presents "
             "itself. Composed into every conversation, before the steering "
             "instructions. It shapes the tone, never what Gen may do: tool "
             "access and safety rules are set on the bridge, not here. "
             "2,000 characters at most.",
    )

    # Qui voit quels compteurs. Vide = tous les
    # usagers internes, comme avant.
    claude_meter_plan_group_id = fields.Many2one(
        "res.groups", string="Plan counters visible to",
        config_parameter="bf_claude_chat.meter_plan_group_id",
        help="Who sees the Claude plan windows (session, week). Empty: every "
             "internal user.",
    )
    claude_meter_usage_group_id = fields.Many2one(
        "res.groups", string="Conversation tokens visible to",
        config_parameter="bf_claude_chat.meter_usage_group_id",
        help="Who sees the tokens a conversation used. Empty: every internal user.",
    )
    claude_meter_cost_group_id = fields.Many2one(
        "res.groups", string="Cost visible to",
        config_parameter="bf_claude_chat.meter_cost_group_id",
        help="Who sees what a conversation would cost at API rates. Empty: "
             "every internal user.",
    )

    # Le forfait Max qui paie les tours de cette
    # instance. La bascule vit dans le pont : ces deux champs la lisent et
    # l'écrivent par lui, ils ne la copient pas en base.
    claude_plan_account_id = fields.Many2one(
        "claude.account", string="Plan paying for Gen",
        compute="_compute_plan_switch", inverse="_inverse_plan_switch",
        help="Empty: this instance's own plan. Another account: Gen turns are "
             "paid by that Max plan, for the users below (all if empty). Only "
             "the accounts the bridge allows for this instance are accepted.",
    )
    claude_plan_user_ids = fields.Many2many(
        "res.users", string="Switched users",
        compute="_compute_plan_switch", inverse="_inverse_plan_switch",
        domain=[("share", "=", False)],
        help="Users whose Gen turns go to the plan above. Empty: everyone.",
    )
    claude_plan_switch_error = fields.Char(compute="_compute_plan_switch")
    claude_plan_switch_available = fields.Boolean(compute="_compute_plan_switch")
    # Ce que le pont disait À L'OUVERTURE du formulaire.
    # Les réglages renvoient tous leurs champs à chaque enregistrement ; sans
    # cet instantané, un formulaire resté ouvert pendant une bascule faite
    # ailleurs (skill, autre admin) la retirait en enregistrant autre chose.
    # Calculé avec les autres (une seule lecture du pont, et seulement quand le
    # formulaire le demande) ; l'inverse vide garde la valeur RENVOYÉE par le
    # formulaire pendant l'enregistrement.
    claude_plan_switch_snapshot = fields.Char(
        compute="_compute_plan_switch", inverse="_inverse_plan_switch_snapshot")

    # ⚠️ Une dépendance réelle : sans elle, le chargement du formulaire
    # (`onchange` d'un enregistrement neuf) ne calcule pas ces champs et le
    # bloc restait masqué.
    @api.depends("company_id")
    def _compute_plan_switch(self):
        etat = self.env["claude.account"]._plan_switch_read()
        for rec in self:
            rec.claude_plan_account_id = etat["account"]
            rec.claude_plan_user_ids = etat["users"]
            rec.claude_plan_switch_error = etat["error"]
            rec.claude_plan_switch_available = etat["available"]
            rec.claude_plan_switch_snapshot = "" if etat["error"] else json.dumps(
                self.env["claude.account"]._plan_switch_key(etat["account"], etat["users"]))

    def _inverse_plan_switch_snapshot(self):
        """Rien : la valeur renvoyée sert seulement à `_inverse_plan_switch`."""

    def _inverse_plan_switch(self):
        Compte = self.env["claude.account"]
        for rec in self:
            if Compte._plan_switch_unchanged(rec.claude_plan_switch_snapshot,
                                             rec.claude_plan_account_id,
                                             rec.claude_plan_user_ids):
                continue
            Compte._plan_switch_write(rec.claude_plan_account_id, rec.claude_plan_user_ids)

    # A constraint rather than a set_values() override: the settings form saves
    # the record before it runs set_values(), so the refusal lands on the save
    # itself, and set_values() on a full instance reaches every module's
    # override, some of which call out to the network (onlyoffice_odoo checks
    # its document server), which no test should trigger.
    @api.constrains("claude_personality")
    def _check_personality_size(self):
        for rec in self:
            size = len((rec.claude_personality or "").strip())
            if size > PERSONALITY_MAX_CHARS:
                raise ValidationError(_(
                    "Gen's personality is limited to %(max)s characters; this "
                    "text has %(size)s. Shorten it: the bridge cuts at the same "
                    "length, and a cut would be silent.",
                    max=PERSONALITY_MAX_CHARS, size=size))
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

