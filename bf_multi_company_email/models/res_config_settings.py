"""Signature settings, per company, in the standard Settings screen.

Everything here is a plain related field on ``res.company`` with
``readonly=False`` — the Odoo convention for a company setting. The screen
follows the company selector at the top of Settings, so a multi-company
database configures each one in turn without a second UI.
"""

from odoo import _, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_signature_template_id = fields.Many2one(
        related="company_id.bf_signature_template_id",
        string="Email signature template",
        domain="[('bf_is_signature', '=', True)]",
        readonly=False,
    )
    bf_signature_enforce = fields.Boolean(
        related="company_id.bf_signature_enforce",
        string="Enforce the signature",
        readonly=False,
    )
    bf_signature_auto_apply = fields.Boolean(
        related="company_id.bf_signature_auto_apply",
        string="Propagate template changes",
        readonly=False,
    )
    bf_signature_logo_url = fields.Char(
        related="company_id.bf_signature_logo_url",
        string="Signature logo (URL)",
        readonly=False,
    )
    bf_signature_logo_width = fields.Integer(
        related="company_id.bf_signature_logo_width",
        string="Logo width (px)",
        readonly=False,
    )
    bf_signature_accent_color = fields.Char(
        related="company_id.bf_signature_accent_color",
        string="Accent colour",
        readonly=False,
    )
    bf_signature_text_color = fields.Char(
        related="company_id.bf_signature_text_color",
        string="Text colour",
        readonly=False,
    )
    bf_signature_muted_color = fields.Char(
        related="company_id.bf_signature_muted_color",
        string="Secondary colour",
        readonly=False,
    )
    bf_signature_font = fields.Char(
        related="company_id.bf_signature_font",
        string="Font",
        readonly=False,
    )
    bf_signature_tollfree = fields.Char(
        related="company_id.bf_signature_tollfree",
        string="Toll-free number",
        readonly=False,
    )
    bf_signature_tagline = fields.Char(
        related="company_id.bf_signature_tagline",
        string="Brand signature",
        readonly=False,
    )
    bf_signature_disclaimer = fields.Html(
        related="company_id.bf_signature_disclaimer",
        string="Confidentiality notice",
        readonly=False,
    )
    bf_signature_disclaimer_size = fields.Selection(
        related="company_id.bf_signature_disclaimer_size",
        string="Notice size",
        readonly=False,
    )
    bf_signature_user_count = fields.Integer(
        related="company_id.bf_signature_user_count",
        string="Driven signatures",
    )

    def action_bf_apply_signatures(self):
        self.ensure_one()
        return self.company_id.action_bf_apply_signatures()

    def action_bf_open_signature_links(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Signature quick links"),
            "res_model": "bf.signature.link",
            "view_mode": "list,form",
            "domain": [("company_id", "=", self.company_id.id)],
            "context": {"default_company_id": self.company_id.id},
        }
