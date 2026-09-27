# -*- coding: utf-8 -*-
"""Un lien de partage : une liste, un destinataire, un jeton secret, une échéance."""
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

ECHEANCE_JOURS = 90
ELEMENTS_MAX = 50  # ce que la page et le RSS montrent, du plus récent au plus ancien


class FluxPartage(models.Model):
    _name = "bf.flux.partage"
    _description = "Lien de partage externe d'une liste de flux"
    _order = "create_date desc"

    name = fields.Char(
        "Pour qui", required=True,
        help="Le destinataire ou l'usage du lien : « Client X, projet Y ».")
    liste_id = fields.Many2one(
        "bf.flux.liste", string="Liste", required=True, ondelete="cascade", index=True)
    jeton = fields.Char(
        "Jeton", required=True, readonly=True, copy=False, index=True,
        default=lambda s: secrets.token_urlsafe(32),
        groups="bf_flux.group_flux_gestion")
    active = fields.Boolean(
        "Actif", default=True,
        help="Décocher révoque le lien : il répond aussitôt « introuvable ».")
    date_echeance = fields.Date(
        "Échéance", default=lambda s: fields.Date.today() + timedelta(days=ECHEANCE_JOURS),
        help="Vide : sans échéance. Un lien oublié reste ouvert, d'où l'échéance par défaut.")
    url = fields.Char("Lien de la page", compute="_compute_url")
    url_rss = fields.Char("Lien RSS", compute="_compute_url")
    acces_count = fields.Integer("Visites", readonly=True)
    dernier_acces = fields.Datetime("Dernier accès", readonly=True)
    etat = fields.Selection(
        [("ouvert", "Ouvert"), ("echu", "Échu"), ("revoque", "Révoqué")],
        compute="_compute_etat", string="État")

    _sql_constraints = [
        ("jeton_unique", "UNIQUE(jeton)", "Ce jeton existe déjà."),
    ]

    @api.depends("jeton")
    def _compute_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        for part in self:
            jeton = part.sudo().jeton
            part.url = f"{base}/flux/partage/{jeton}" if jeton else False
            part.url_rss = f"{part.url}/rss" if jeton else False

    @api.depends("active", "date_echeance")
    def _compute_etat(self):
        aujourd_hui = fields.Date.today()
        for part in self:
            if not part.active:
                part.etat = "revoque"
            elif part.date_echeance and part.date_echeance < aujourd_hui:
                part.etat = "echu"
            else:
                part.etat = "ouvert"

    def action_regenerer(self):
        """Un nouveau jeton : l'ancien lien cesse de répondre."""
        # Le jeton s'écrit en superutilisateur (le champ est réservé) : sans
        # cette garde, n'importe quel compte coupait les liens des clients.
        if not (self.env.su or self.env.user.has_group("bf_flux.group_flux_gestion")):
            raise AccessError(_("Régénérer un lien est réservé à la gestion des flux."))
        for part in self:
            part.sudo().jeton = secrets.token_urlsafe(32)
        return True

    def action_revoquer(self):
        self.write({"active": False})
        return True

    @api.model
    def _flux_ouvert(self, jeton):
        """Le partage ouvert de ce jeton, ou rien. Même réponse pour un jeton
        inconnu, échu ou révoqué : rien ne dit lequel."""
        if not jeton or len(jeton) < 20:
            return self.browse()
        part = self.sudo().search([("jeton", "=", jeton)], limit=1)
        if not part or part.etat != "ouvert" or not part.liste_id.active:
            return self.browse()
        return part

    def _flux_elements(self):
        """Les éléments montrés : retenus par la liste, les plus récents d'abord."""
        self.ensure_one()
        retenues = self.env["bf.flux.retenue"].sudo().search([
            ("liste_id", "=", self.liste_id.id), ("etat", "=", "retenu"),
        ], order="date_publication desc, id desc", limit=ELEMENTS_MAX)
        # Défense en profondeur : la lecture du flux écarte déjà les liens
        # qui ne sont pas http(s), la page ne les montre pas davantage.
        return retenues.element_id.filtered(
            lambda e: (e.lien or "").lower().startswith(("http://", "https://")))

    def _flux_sources_de(self, element):
        """Les sources de l'élément que la liste partagée lit elle-même : une
        source d'une autre liste (un flux privé à jeton) ne sort pas."""
        self.ensure_one()
        return element.sudo().source_ids & self.liste_id.sudo().source_ids

    def _flux_compter(self):
        self.ensure_one()
        self.sudo().write({
            "acces_count": self.acces_count + 1,
            "dernier_acces": fields.Datetime.now(),
        })


class FluxListe(models.Model):
    _inherit = "bf.flux.liste"

    partage_ids = fields.One2many(
        "bf.flux.partage", "liste_id", string="Liens de partage",
        context={"active_test": False})
