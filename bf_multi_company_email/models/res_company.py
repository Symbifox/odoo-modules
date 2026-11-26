from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_signature_template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Email signature template",
        domain="[('bf_is_signature', '=', True)]",
        ondelete="set null",
        help="Template rendered on the profile of every person in this "
             "company to produce a uniform HTML signature. The template "
             "is written on the res.users model (object = the user).",
    )

    # ------------------------------------------------------------------
    # Signature policy (18.0.2.0.0)
    # ------------------------------------------------------------------
    bf_signature_enforce = fields.Boolean(
        string="Enforce the signature",
        help="Nobody can edit their signature by hand any more: the field "
             "is locked in preferences and any write is replaced by the "
             "rendered template. Administrators (Settings) stay free.",
    )
    bf_signature_auto_apply = fields.Boolean(
        string="Propagate the changes",
        default=True,
        help="Regenerates the signatures as soon as the template or the "
             "branding elements change. Cleared, you have to click "
             "\"Apply to everyone\".",
    )

    # -- brand elements the template reads -----------------------------
    bf_signature_logo_url = fields.Char(
        string="Logo (URL)",
        help="Public address of the logo. An image sent as an attachment "
             "or as a data: URI is blocked or degraded by most clients; a "
             "stable URL survives, and overwriting the file behind it "
             "updates every signature already pasted elsewhere.",
    )
    bf_signature_logo_width = fields.Integer(
        string="Logo width (px)",
        default=110,
    )
    bf_signature_accent_color = fields.Char(
        string="Accent colour",
        # Neutral out of the box. A module installed by another organisation
        # should not open in the publisher's brand; a company that has already
        # chosen a colour keeps it, because the seeding only overwrites a value
        # still equal to this default (see hooks._untouched).
        default="#3C6E9F",
    )
    bf_signature_text_color = fields.Char(
        string="Text colour",
        default="#222222",
        # Depuis la 18.0.5.1.0, ce champ ne porte plus seulement du texte : il
        # est aussi le FOND des boutons de liens rapides, dont le libellé est
        # blanc en dur dans les gabarits livrés. Une couleur claire ici rend
        # donc ce libellé illisible — le défaut que la 18.0.5.1.0 corrige,
        # exactement à l'envers. Choisir le libellé selon la luminance du fond
        # demande une clé de plus dans `res.users.bf_signature_data()`, donc
        # du Python : c'est une suite à faire, au prochain redémarrage du
        # module, et pas quelque chose qu'une écriture de gabarit peut livrer.
        help="Colour of the signature's main text. ⚠ It is also the "
             "background of the quick-link buttons, whose labels are "
             "white: a light colour here would make those labels "
             "unreadable. Keep a dark colour — WCAG AA asks for 4.5:1 "
             "contrast against white for normal text.",
    )
    bf_signature_muted_color = fields.Char(
        string="Secondary colour",
        default="#666666",
    )
    bf_signature_font = fields.Char(
        string="Font",
        # A system stack by default: a brand typeface is not installed on the
        # recipient's machine, and a public module has no business assuming
        # the publisher's. Companies that want theirs set it once.
        default="-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
        help="CSS font stack. Always end with a universal font: no brand "
             "font is installed on the recipient's side.",
    )
    bf_signature_tollfree = fields.Char(
        string="Toll-free number",
        help="Shown next to the person's direct phone.",
    )
    bf_signature_tagline = fields.Char(
        string="Brand signature",
        help="One line under the block, optional.",
    )
    bf_signature_disclaimer = fields.Html(
        string="Confidentiality notice",
        translate=True,
        sanitize=True,
        help="The legal block some organisations add to the foot of every "
             "email: confidentiality, unintended recipient, duty to "
             "destroy. ⚠️ It travels WITH the signature, so it appears "
             "wherever the signature appears. Products that add it to all "
             "outgoing mail, including short replies with no signature, "
             "do so at the gateway — that is neither the same place nor "
             "the same tool. The field is translatable: each "
             "correspondent receives the version of the language recorded "
             "on their record.",
    )
    bf_signature_disclaimer_size = fields.Selection(
        selection=[
            ("tiny", "Very small (9 px)"),
            ("small", "Small (10 px)"),
            ("normal", "Normal (12 px)"),
        ],
        string="Notice size",
        default="tiny",
        help="A legal notice is rarely read and takes up all the room. "
             "Organisations that include one set it small.",
    )
    bf_signature_link_ids = fields.One2many(
        comodel_name="bf.signature.link",
        inverse_name="company_id",
        string="Quick links",
        help="Shown in the signature footer for the whole company.",
    )
    bf_signature_user_count = fields.Integer(
        string="Driven signatures",
        compute="_compute_bf_signature_user_count",
    )

    # Per-company mail routing. Surfaced to the standard mail.* config
    # parameters by the ir.config_parameter override (see ir_config_parameter.py).
    # bf_-prefixed names keep this module fully decoupled from the legacy
    # ous_multi_company_email fields so install/uninstall order is free.
    bf_catchall_domain = fields.Char(
        string="Catchall domain (company)",
        help="Overrides mail.catchall.domain when this company is active.",
    )
    bf_catchall_alias = fields.Char(
        string="Catchall alias (company)",
        help="Overrides mail.catchall.alias when this company is active.",
    )
    bf_bounce_alias = fields.Char(
        string="Bounce alias (company)",
        help="Overrides mail.bounce.alias when this company is active.",
    )
    bf_default_from = fields.Char(
        string="Default sender (company)",
        help="Overrides mail.default.from when this company is active.",
    )

    # ------------------------------------------------------------------
    def _compute_bf_signature_user_count(self):
        Users = self.env["res.users"].sudo()
        for company in self:
            company.bf_signature_user_count = Users.search_count([
                ("share", "=", False),
                ("company_id", "=", company.id),
            ]) if company.id else 0

    def _bf_signature_users(self):
        """Internal users whose signature this company drives."""
        return self.env["res.users"].sudo().search([
            ("share", "=", False),
            ("company_id", "in", self.ids),
        ])

    def action_bf_apply_signatures(self):
        """Re-render every signature this company drives."""
        # Réécrit, en sudo, la signature de toute la société, y compris quand
        # la propagation est coupée le temps de préparer un gabarit.
        if not self.env.user.has_group("base.group_erp_manager"):
            raise AccessError(_(
                "Applying the signature to the whole company is reserved "
                "to administrators."))
        users = self._bf_signature_users()
        users._bf_apply_company_signature()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Signatures applied"),
                "message": _("%s signature(s) regenerated.", len(users)),
                "type": "success",
                "sticky": False,
            },
        }

    # Writing any of these changes what the template renders, so the stored
    # signatures are stale the instant they are saved.
    _BF_SIGNATURE_TRIGGERS = (
        "bf_signature_template_id", "bf_signature_logo_url",
        "bf_signature_logo_width", "bf_signature_accent_color",
        "bf_signature_text_color", "bf_signature_muted_color",
        "bf_signature_font", "bf_signature_tollfree", "bf_signature_tagline",
        "bf_signature_disclaimer", "bf_signature_disclaimer_size",
        "name", "website", "email",
    )

    def write(self, vals):
        res = super().write(vals)
        if any(key in vals for key in self._BF_SIGNATURE_TRIGGERS):
            targets = self.filtered("bf_signature_auto_apply")
            if targets:
                targets._bf_signature_users()._bf_apply_company_signature()
        return res
