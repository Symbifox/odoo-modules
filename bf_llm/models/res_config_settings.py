from odoo import fields, models

from . import _fernet


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_llm_default_provider_id = fields.Many2one(
        "bf.llm.provider",
        string="Default LLM Provider",
        compute="_compute_bf_llm_default_provider",
        readonly=True,
    )
    bf_llm_default_provider_name = fields.Char(
        related="bf_llm_default_provider_id.name",
        string="Default LLM Provider Name",
        readonly=True,
    )

    def _compute_bf_llm_default_provider(self):
        default = (
            self.env["bf.llm.provider"]
            .sudo()
            .search([("is_default", "=", True), ("enabled", "=", True)], limit=1)
        )
        for rec in self:
            rec.bf_llm_default_provider_id = default

    def action_open_bf_llm_providers(self):
        return self.env["ir.actions.actions"]._for_xml_id(
            "bf_llm.action_bf_llm_provider"
        )

    # --- Encryption helpers (parity with bf_claude_chat; delegate to _fernet) ---

    @staticmethod
    def _get_encryption_key():
        return _fernet.get_encryption_key()

    @staticmethod
    def _encrypt_api_key(value):
        return _fernet.encrypt(value)

    @staticmethod
    def _decrypt_api_key(env, encrypted):
        return _fernet.decrypt(encrypted)
