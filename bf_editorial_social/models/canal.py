# -*- coding: utf-8 -*-
"""Un canal : un compte sur un réseau, avec sa langue et ses identifiants."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import _fernet


class SocialChannel(models.Model):
    _name = "bf.social.channel"
    _description = "Canal de diffusion"
    _inherit = ["mail.thread"]
    _order = "sequence, id"

    name = fields.Char(string="Nom", required=True, tracking=True)
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)
    network = fields.Selection(
        selection="_selection_network", string="Réseau", required=True,
        tracking=True,
    )
    handle = fields.Char(
        string="Pseudonyme", required=True, tracking=True,
        help="Le pseudonyme public du compte, tel que le réseau le connaît.",
    )
    lang_id = fields.Many2one(
        "res.lang", string="Langue publiée", required=True,
        help="Le créneau de langue de l'article qui part sur ce canal. Un"
             " compte tenu dans une seule langue reste lisible.",
    )
    company_id = fields.Many2one(
        "res.company", string="Société", default=lambda s: s.env.company,
        required=True, index=True,
    )
    calendar_ids = fields.Many2many(
        "bf.editorial.calendar", string="Calendriers alimentés",
    )
    utm_source_id = fields.Many2one("utm.source", string="Source UTM")
    utm_medium_id = fields.Many2one("utm.medium", string="Médium UTM")

    # --- identifiants -----------------------------------------------------
    login = fields.Char(
        string="Identifiant de connexion",
        help="Souvent le pseudonyme complet. Ce que le réseau attend comme"
             " nom d'usager pour un mot de passe d'application.",
    )
    secret = fields.Char(
        string="Mot de passe d'application",
        compute="_compute_secret", inverse="_inverse_secret",
        groups="bf_editorial.group_editorial_manager",
        help="Chiffré hors de la base. Un mot de passe d'application se"
             " révoque sans toucher au mot de passe du compte : ne jamais"
             " poser ici le mot de passe principal.",
    )
    credentials_state = fields.Selection(
        [("unknown", "Jamais vérifiés"), ("ok", "Valides"), ("ko", "Refusés")],
        string="État des identifiants", default="unknown", readonly=True,
        copy=False, tracking=True,
    )
    credentials_message = fields.Char(string="Dernier retour", readonly=True, copy=False)
    last_checked = fields.Datetime(string="Vérifiés le", readonly=True, copy=False)

    # --- dérivé -----------------------------------------------------------
    post_ids = fields.One2many("bf.social.post", "channel_id", string="Billets")
    post_count = fields.Integer(string="Billets", compute="_compute_post_count")
    body_limit = fields.Integer(
        string="Limite de caractères", compute="_compute_limits",
    )

    # La langue fait partie de la clé : un même compte se tient légitimement
    # en plusieurs langues. Une page LinkedIn est LA MÊME page en français et
    # en anglais, et le module exige justement une entrée par langue publiée —
    # sans `lang_id` ici, la contrainte interdisait ce que le reste du modèle
    # tient pour normal.
    _sql_constraints = [
        ("handle_unique_per_network_lang",
         "UNIQUE(network, handle, company_id, lang_id)",
         "Ce pseudonyme est déjà déclaré pour ce réseau dans cette langue."),
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
        return sorted(reseaux) or [("none", _("Aucun connecteur installé"))]

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
            canal.message_post(body=_("Vérification des identifiants : %s", message))
        return True

    def _connector(self):
        self.ensure_one()
        return self.env["bf.social.connector"]._for_network(self.network)
