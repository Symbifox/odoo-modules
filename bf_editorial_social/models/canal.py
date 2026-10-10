# -*- coding: utf-8 -*-
"""Un canal : un compte sur un réseau, avec sa langue et ses identifiants."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import _fernet


class SocialChannel(models.Model):
    _name = "bf.social.channel"
    _description = "Publishing channel"
    _inherit = ["mail.thread"]
    _order = "sequence, id"

    name = fields.Char(string="Name", required=True, tracking=True)
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)
    network = fields.Selection(
        selection="_selection_network", string="Network", required=True,
        tracking=True,
    )
    handle = fields.Char(
        string="Handle", required=True, tracking=True,
        help="The account's public handle, as the network knows it.",
    )
    lang_id = fields.Many2one(
        "res.lang", string="Published language", required=True,
        help="The article's language slot that goes out on this channel. "
             "An account kept in a single language stays readable.",
    )
    company_id = fields.Many2one(
        "res.company", string="Company", default=lambda s: s.env.company,
        required=True, index=True,
    )
    calendar_ids = fields.Many2many(
        "bf.editorial.calendar", string="Calendars fed",
    )
    utm_source_id = fields.Many2one("utm.source", string="UTM source")
    utm_medium_id = fields.Many2one("utm.medium", string="UTM medium")

    # --- identifiants -----------------------------------------------------
    login = fields.Char(
        string="Login",
        help="Often the full handle. What the network expects as the "
             "username for an app password.",
    )
    secret = fields.Char(
        string="App password",
        compute="_compute_secret", inverse="_inverse_secret",
        groups="bf_editorial.group_editorial_manager",
        help="Encrypted outside the database. An app password can be "
             "revoked without touching the account password: never put "
             "the main password here.",
    )
    credentials_state = fields.Selection(
        [("unknown", "Never checked"), ("ok", "Valid"), ("ko", "Rejected")],
        string="Credentials status", default="unknown", readonly=True,
        copy=False, tracking=True,
    )
    credentials_message = fields.Char(string="Last response", readonly=True, copy=False)
    last_checked = fields.Datetime(string="Checked on", readonly=True, copy=False)

    # --- dérivé -----------------------------------------------------------
    post_ids = fields.One2many("bf.social.post", "channel_id", string="Posts")
    post_count = fields.Integer(string="Posts", compute="_compute_post_count")
    body_limit = fields.Integer(
        string="Character limit", compute="_compute_limits",
    )

    # La langue fait partie de la clé : un même compte se tient légitimement
    # en plusieurs langues. Une page LinkedIn est LA MÊME page en français et
    # en anglais, et le module exige justement une entrée par langue publiée —
    # sans `lang_id` ici, la contrainte interdisait ce que le reste du modèle
    # tient pour normal.
    _sql_constraints = [
        ("handle_unique_per_network_lang",
         "UNIQUE(network, handle, company_id, lang_id)",
         "This handle is already declared for this network in this language."),
    ]

    @api.model
    def _selection_network(self):
        """Les réseaux réellement installés, pas une liste d'intentions."""
        reseaux = []
        for nom in self.env:
            if nom.startswith("bf.social.connector.") and nom.count(".") == 3:
                cle = nom.rsplit(".", 1)[1]
                lib = getattr(self.env[nom], "_network_label", cle.capitalize())
                reseaux.append((cle, lib))
        return sorted(reseaux) or [("none", _("No connector installed"))]

    def _secret_param(self):
        self.ensure_one()
        return "bf_editorial_social.secret.%s" % self.id

    @api.depends("network", "handle")
    def _compute_secret(self):
        Param = self.env["ir.config_parameter"].sudo()
        for canal in self:
            canal.secret = _fernet.decrypt(Param.get_param(canal._secret_param(), "")) \
                if isinstance(canal.id, int) and canal.id else ""

    def _inverse_secret(self):
        Param = self.env["ir.config_parameter"].sudo()
        for canal in self:
            if not (isinstance(canal.id, int) and canal.id):
                continue
            Param.set_param(canal._secret_param(), _fernet.encrypt(canal.secret or ""))

    def _decrypt_secret(self):
        self.ensure_one()
        Param = self.env["ir.config_parameter"].sudo()
        return _fernet.decrypt(Param.get_param(self._secret_param(), ""))

    def _compute_post_count(self):
        for canal in self:
            canal.post_count = len(canal.post_ids)

    @api.depends("network")
    def _compute_limits(self):
        for canal in self:
            try:
                lim = self.env["bf.social.connector"]._for_network(canal.network)._limits()
                canal.body_limit = lim.get("body_chars") or 0
            except Exception:
                canal.body_limit = 0

    # --- actions ----------------------------------------------------------
    def action_check_credentials(self):
        for canal in self:
            connecteur = self.env["bf.social.connector"]._for_network(canal.network)
            ok, message = connecteur._validate_credentials(canal)
            canal.write({
                "credentials_state": "ok" if ok else "ko",
                "credentials_message": (message or "")[:255],
                "last_checked": fields.Datetime.now(),
            })
            canal.message_post(body=_("Credentials check: %s", message))
        return True

    def _connector(self):
        self.ensure_one()
        return self.env["bf.social.connector"]._for_network(self.network)
