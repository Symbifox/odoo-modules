# -*- coding: utf-8 -*-
from odoo import _, api, fields, models

from odoo.addons.bf_floorplan.models.genres import taille_element

# La nature de la forme, d'après le type de l'appareil.
GENRE_PAR_TYPE = {
    "workstation": "poste", "laptop": "poste", "server": "serveur",
    "vm": "serveur", "mobile": "autre", "other": "autre",
}


class BfFloorplanElement(models.Model):
    _inherit = "bf.floorplan.element"

    endpoint_id = fields.Many2one(
        "hosting.endpoint", string="Appareil du parc", ondelete="set null",
        tracking=True, index=True,
        help="L'appareil que cette forme représente. Cliquer la forme ouvre sa fiche.")
    server_id = fields.Many2one(
        "hosting.server", string="Serveur", ondelete="set null", tracking=True,
        index=True, help="Le serveur que cette forme représente.")

    _sql_constraints = [
        ("endpoint_unique", "unique(endpoint_id)",
         "Cet appareil est déjà posé sur un plan."),
        ("server_unique", "unique(server_id)",
         "Ce serveur est déjà posé sur un plan."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        # le nom se recopie comme un fait, avec ou sans droits sur le parc
        Endpoint = self.env["hosting.endpoint"].sudo()
        Server = self.env["hosting.server"].sudo()
        for vals in vals_list:
            if vals.get("endpoint_id") and not vals.get("name"):
                vals["name"] = Endpoint.browse(vals["endpoint_id"]).name
            if vals.get("server_id") and not vals.get("name"):
                vals["name"] = Server.browse(vals["server_id"]).name
        return super().create(vals_list)

    @api.onchange("endpoint_id")
    def _onchange_endpoint_id(self):
        for el in self:
            if not el.endpoint_id:
                continue
            el.name = el.name or el.endpoint_id.name
            genre = GENRE_PAR_TYPE.get(el.endpoint_id.endpoint_type, "autre")
            if el.genre != genre:
                el.genre = genre
                el.w, el.h = taille_element(genre)

    @api.onchange("server_id")
    def _onchange_server_id(self):
        for el in self:
            if not el.server_id:
                continue
            el.name = el.name or el.server_id.name
            if el.genre != "serveur":
                el.genre = "serveur"
                el.w, el.h = taille_element("serveur")

    # --- crochets -----------------------------------------------------------

    # Le plan se lit sans les droits de la gestion d'hébergement : ce qu'il
    # montre d'un appareil (nom, état, système) se lit donc en sudo, comme un
    # tableau d'affichage. La FICHE, elle, ne s'ouvre que si on y a accès :
    # sans accès, la forme n'annonce pas de cible et le clic ouvre l'élément.
    def _cible(self):
        self.ensure_one()
        if self.endpoint_id and self.endpoint_id.has_access("read"):
            return {"modele": "hosting.endpoint", "id": self.endpoint_id.id,
                    "nom": self.endpoint_id.display_name}
        if self.server_id and self.server_id.has_access("read"):
            return {"modele": "hosting.server", "id": self.server_id.id,
                    "nom": self.server_id.display_name}
        return super()._cible()

    def _teinte(self):
        self.ensure_one()
        ep = self.endpoint_id.sudo()
        if ep:
            if ep.lifecycle_state in ("in_repair", "retired") or ep.os_eol_tag:
                return "alerte"
            if ep.warranty_end and ep.warranty_end < fields.Date.context_today(self):
                return "attention"
            return ""
        srv = self.server_id.sudo()
        if srv:
            if srv.state == "decommissioned":
                return "alerte"
            if srv.state == "maintenance":
                return "attention"
        return super()._teinte()

    def _infos(self):
        infos = super()._infos()
        ep = self.endpoint_id.sudo()
        if ep:
            if ep.assigned_display:
                infos.append(ep.assigned_display)
            if ep.os:
                infos.append(dict(ep._fields["os"].selection).get(ep.os, ep.os))
            if ep.os_eol_tag:
                infos.append(_("Système en fin de vie"))
            if ep.lifecycle_state != "deployed":
                infos.append(dict(ep._fields["lifecycle_state"].selection)
                             .get(ep.lifecycle_state, ep.lifecycle_state))
            if ep.warranty_end and ep.warranty_end < fields.Date.context_today(self):
                infos.append(_("Garantie échue le %s") % ep.warranty_end.isoformat())
        srv = self.server_id.sudo()
        if srv:
            if srv.hostname:
                infos.append(srv.hostname)
            infos.append(_("%s service(s)") % srv.service_count)
            if srv.state != "active":
                infos.append(dict(srv._fields["state"].selection).get(srv.state, srv.state))
        return infos
