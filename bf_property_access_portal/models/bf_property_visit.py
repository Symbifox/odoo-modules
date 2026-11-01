"""Le visiteur annoncé au portail peut recevoir un accès temporaire.

Annoncer un visiteur prévient le syndicat ; avec la case « Lui donner un accès
temporaire », l'hôte demande aussi un accès aux portes COMMUNES de l'immeuble
(entrée, ascenseur), pour la plage de la visite seulement, plafonnée par
l'organisation. Jamais la porte d'un logement, ni le garage, ni une boîte à
colis : ce sont des décisions du gestionnaire.
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

VISITOR_POINT_TYPES = ("entrance", "elevator")
DEFAULT_HOURS = 2
EARLY = timedelta(minutes=15)
# Pas d'accès donné des mois d'avance par le portail.
HORIZON = timedelta(days=30)


class BfPropertyVisit(models.Model):
    _inherit = "bf.property.visit"

    access_pass_id = fields.Many2one(
        "bf.property.access.pass", string="Accès temporaire", readonly=True, copy=False,
        ondelete="set null",
    )
    access_pass_state = fields.Char(
        string="Accès du visiteur", compute="_compute_access_pass_state",
        help="Lu en élévation : l'hôte n'a pas accès aux accès temporaires eux-mêmes.",
    )

    @api.depends("access_pass_id.state")
    def _compute_access_pass_state(self):
        states = dict(self.env["bf.property.access.pass"]._fields["state"]
                      ._description_selection(self.env))
        for visit in self:
            temp = visit.sudo().access_pass_id
            visit.access_pass_state = states.get(temp.state, "") if temp else ""

    def _bf_request_access(self, until=None):
        """Demandé par l'hôte en annonçant son visiteur (le contrôleur du portail)."""
        self.ensure_one()
        visit = self.sudo()
        if visit.host_partner_id != self.env.user.partner_id and not self.env.user.has_group(
            "bf_property_core.group_bf_property_manager"
        ):
            raise AccessError(_("Seul l'hôte donne un accès à son visiteur."))
        org = visit.organisation_id
        if not org.visitor_pass_enabled:
            raise UserError(_("Cette organisation ne permet pas de donner un accès aux visiteurs."))
        if visit.state != "expected" or visit.access_pass_id:
            raise UserError(_("« %s » n'attend pas d'accès.") % visit.name)
        building = visit.building_id or visit.unit_id.building_id
        if not building:
            # « La mienne » au portail : l'immeuble où l'hôte réside, s'il n'y en a qu'un.
            homes = self.env["bf.property.access.resident"].sudo().search([
                ("partner_id", "=", visit.host_partner_id.id),
                ("organisation_id", "=", visit.organisation_id.id),
                ("state", "in", ("active", "departure_pending"))])
            if len(homes.building_id) == 1:
                building = homes.building_id
                visit.write({"building_id": building.id,
                             "unit_id": homes.unit_id[:1].id if len(homes.unit_id) == 1 else False})
        if not building:
            raise UserError(_("Indiquez la fraction : l'accès dépend de l'immeuble."))
        # 🔴 L'hôte doit RÉSIDER dans l'immeuble : un résident dont le départ est
        # confirmé (même si le dossier le nomme encore) ou un propriétaire qui
        # loue ne donnent pas d'accès.
        present = self.env["bf.property.access.resident"].sudo().search_count([
            ("partner_id", "=", visit.host_partner_id.id), ("building_id", "=", building.id),
            ("state", "in", ("active", "departure_pending"))])
        if not present:
            raise UserError(_("Seul un résident de l'immeuble donne un accès à son visiteur."))
        points = self.env["bf.property.access.point"].sudo().search([
            ("building_id", "=", building.id), ("unit_id", "=", False),
            ("point_type", "in", VISITOR_POINT_TYPES)])
        if not points:
            raise UserError(_("L'immeuble n'a aucune porte commune où donner un accès."))
        start = visit.date_expected - EARLY
        end = until or visit.date_expected_end or (visit.date_expected + timedelta(hours=DEFAULT_HOURS))
        cap = start + timedelta(hours=max(org.visitor_pass_max_hours, 1))
        end = min(end, cap)
        if end <= max(start, fields.Datetime.now()):
            raise UserError(_("La plage de la visite est déjà passée."))
        if start > fields.Datetime.now() + HORIZON:
            raise UserError(_("Un accès se demande au plus 30 jours d'avance."))
        temp = self.env["bf.property.access.pass"].sudo().create({
            "kind": "visitor", "holder_name": visit.visitor_name,
            "building_id": building.id, "point_ids": [(6, 0, points.ids)],
            "credential_kind": "pin", "date_from": start, "date_to": end,
            "note": _("Visiteur de %(host)s (%(visit)s), demandé au portail.") % {
                "host": visit.host_partner_id.name, "visit": visit.name},
        })
        temp._bf_confirm()
        visit.write({"access_pass_id": temp.id})
        return temp

    def write(self, vals):
        """🔴 Annuler ou déplacer la visite par un autre chemin que le bouton
        (l'hôte peut écrire l'état « annulé » et les dates) ferme aussi l'accès :
        il ne reste pas valide pour une visite qui n'a plus lieu à cette heure."""
        moved = {"date_expected", "date_expected_end", "visitor_name"} & set(vals)
        res = super().write(vals)
        if vals.get("state") == "cancelled" or moved:
            why = (_("Visite annulée.") if vals.get("state") == "cancelled"
                   else _("Visite modifiée : annoncez de nouveau le visiteur pour un accès."))
            self.sudo().access_pass_id._bf_cancel(why)
        return res

    def action_cancel(self):
        res = super().action_cancel()
        self.sudo().access_pass_id._bf_cancel(_("Visite annulée par l'hôte."))
        return res

    def action_departed(self):
        res = super().action_departed()
        self.sudo().access_pass_id._bf_end(_("Visiteur reparti."))
        return res


class BfPropertyAccessResident(models.Model):
    _inherit = "bf.property.access.resident"

    def _bf_confirm_departure(self, date_keys):
        """L'hôte qui part n'annonce plus de visiteurs : les accès qu'il a
        donnés dans cet immeuble se ferment avec son départ."""
        res = super()._bf_confirm_departure(date_keys)
        for res_ in self:
            # Il reste résident ailleurs dans l'immeuble (déménagement interne) :
            # ses visiteurs gardent leur accès.
            if self.sudo().search_count([
                    ("partner_id", "=", res_.partner_id.id), ("building_id", "=", res_.building_id.id),
                    ("state", "in", ("active", "departure_pending"))]):
                continue
            visits = self.env["bf.property.visit"].sudo().search([
                ("host_partner_id", "=", res_.partner_id.id),
                ("access_pass_id.state", "in", ("draft", "confirmed")),
                "|", ("building_id", "=", res_.building_id.id),
                ("unit_id.building_id", "=", res_.building_id.id),
            ])
            visits.access_pass_id._bf_cancel(_("Départ confirmé de l'hôte."))
        return res
