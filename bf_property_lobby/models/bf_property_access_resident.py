"""Le consentement à paraître à l'annuaire de la tablette, et à y être joint.

Loi 25 (art. 9.1) : par défaut, personne n'y paraît. Le résident choisit son
libellé (« A. Exemple », « Famille Exemple », ou rien que le logement) et s'il
accepte que la tablette fasse sonner son téléphone. Le numéro ne quitte jamais le
serveur : la tablette ne voit que le libellé.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .bf_property_lobby_kiosk import _clean_number


class BfPropertyAccessResident(models.Model):
    _inherit = "bf.property.access.resident"

    lobby_listed = fields.Boolean(string="Paraît à l'annuaire de l'entrée", tracking=True,
                                  copy=False)
    # Pas de suivi du libellé : retiré, il ne doit pas survivre dans le fil.
    lobby_label = fields.Char(string="Libellé à l'annuaire", copy=False,
                              help="Ce que la tablette affiche. Vide : « Résident ».")
    lobby_reachable = fields.Boolean(string="Joignable par la tablette", tracking=True,
                                     copy=False, help="La tablette fait sonner son téléphone.")
    lobby_consent_date = fields.Datetime(string="Consentement à l'annuaire", readonly=True,
                                         copy=False)

    lobby_available = fields.Boolean(compute="_compute_lobby_available",
                                     string="L'immeuble a une tablette")

    @api.depends("building_id")
    def _compute_lobby_available(self):
        Kiosk = self.env["bf.property.lobby.kiosk"].sudo()
        with_kiosk = set(Kiosk.search([("building_id", "in", self.building_id.ids)]).building_id.ids)
        for res in self:
            res.lobby_available = res.building_id.id in with_kiosk

    LOBBY_FIELDS = {"lobby_listed", "lobby_label", "lobby_reachable", "lobby_consent_date"}

    @api.model_create_multi
    def create(self, vals_list):
        # Même garde qu'à l'écriture : ni création ni import ne consentent à la
        # place du résident.
        if not self.env.su and any(self.LOBBY_FIELDS & set(v) for v in vals_list):
            raise UserError(_("Paraître à l'annuaire de l'entrée se décide par le résident, "
                              "au portail."))
        return super().create(vals_list)

    def write(self, vals):
        # 🔴 Le consentement est celui du résident : personne ne l'écrit à sa
        # place, gestionnaire compris. Seul le geste du
        # portail (`_bf_set_lobby`, en sudo) le pose.
        if not self.env.su and self.LOBBY_FIELDS & set(vals):
            raise UserError(_("Paraître à l'annuaire de l'entrée se décide par le résident, "
                              "au portail."))
        return super().write(vals)

    def _bf_lobby_number(self):
        self.ensure_one()
        if not self.lobby_reachable:
            return ""
        partner = self.partner_id
        return _clean_number(partner.mobile) or _clean_number(partner.phone)

    def action_lobby_withdraw(self):
        """Retirer de l'annuaire, à la demande du résident (jamais l'y inscrire)."""
        if not self.env.user.has_group("bf_property_core.group_bf_property_manager"):
            raise AccessError(_("Réservé aux gestionnaires."))
        for res in self.filtered("lobby_listed"):
            res._bf_set_lobby(False, False, False, by_manager=True)

    def _bf_set_lobby(self, listed, label, reachable, by_manager=False):
        """Le résident lui-même, au portail (le contrôleur vérifie qui il est), ou le
        gestionnaire qui le retire à sa demande (action_lobby_withdraw)."""
        self.ensure_one()
        vals = {"lobby_listed": bool(listed),
                "lobby_label": ((label or "").strip()[:60] or False) if listed else False,
                "lobby_reachable": bool(listed and reachable)}
        vals["lobby_consent_date"] = fields.Datetime.now() if listed else False
        self.sudo().write(vals)
        if by_manager:
            body = _("Retiré de l'annuaire de l'entrée par %s, à la demande du résident.") % (
                self.env.user.name)
        else:
            body = _("Annuaire de l'entrée : %s (au portail).") % (
                _("paraît") if listed else _("retiré"))
        self.sudo().message_post(body=body)
