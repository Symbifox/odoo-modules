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
        string="Gabarit de signature courriel",
        domain="[('bf_is_signature', '=', True)]",
        readonly=False,
    )
    bf_signature_enforce = fields.Boolean(
        related="company_id.bf_signature_enforce",
        string="Imposer la signature",
        readonly=False,
    )
    bf_signature_auto_apply = fields.Boolean(
        related="company_id.bf_signature_auto_apply",
        string="Propager les changements de gabarit",
        readonly=False,
    )
    bf_signature_logo_url = fields.Char(
        related="company_id.bf_signature_logo_url",
        string="Logo de signature (URL)",
        readonly=False,
    )
    bf_signature_logo_width = fields.Integer(
        related="company_id.bf_signature_logo_width",
        string="Largeur du logo (px)",
        readonly=False,
    )
    bf_signature_accent_color = fields.Char(
        related="company_id.bf_signature_accent_color",
        string="Couleur d'accent",
        readonly=False,
    )
    bf_signature_text_color = fields.Char(
        related="company_id.bf_signature_text_color",
        string="Couleur du texte",
        readonly=False,
    )
    bf_signature_muted_color = fields.Char(
        related="company_id.bf_signature_muted_color",
        string="Couleur secondaire",
        readonly=False,
    )
    bf_signature_font = fields.Char(
        related="company_id.bf_signature_font",
        string="Police",
        readonly=False,
    )
    bf_signature_tollfree = fields.Char(
        related="company_id.bf_signature_tollfree",
        string="Numéro sans frais",
        readonly=False,
    )
    bf_signature_tagline = fields.Char(
        related="company_id.bf_signature_tagline",
        string="Signature de marque",
        readonly=False,
    )
    bf_signature_disclaimer = fields.Html(
        related="company_id.bf_signature_disclaimer",
        string="Avis de confidentialité",
        readonly=False,
    )
    bf_signature_disclaimer_size = fields.Selection(
        related="company_id.bf_signature_disclaimer_size",
        string="Taille de l'avis",
        readonly=False,
    )
    bf_signature_user_count = fields.Integer(
        related="company_id.bf_signature_user_count",
        string="Signatures pilotées",
    )

    def action_bf_apply_signatures(self):
        self.ensure_one()
        return self.company_id.action_bf_apply_signatures()

    def action_bf_open_signature_links(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Liens rapides de signature"),
            "res_model": "bf.signature.link",
            "view_mode": "list,form",
            "domain": [("company_id", "=", self.company_id.id)],
            "context": {"default_company_id": self.company_id.id},
        }
