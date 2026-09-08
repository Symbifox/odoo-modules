# -*- coding: utf-8 -*-
from odoo import _, api, fields, models

from .element import GENRE_PAR_TYPE


class _SurLePlan(models.AbstractModel):
    """Ce que partagent l'appareil et le serveur : un compteur, et le geste
    qui mène au plan, ou qui propose d'y poser la fiche si elle n'y est pas."""

    _name = "bf.floorplan.sur.le.plan"
    _description = "Sur le plan (mixin)"

    floorplan_element_count = fields.Integer(
        compute="_compute_floorplan_element_count", string="Sur le plan")

    @api.depends("floorplan_element_ids")
    def _compute_floorplan_element_count(self):
        for rec in self:
            rec.floorplan_element_count = len(rec.floorplan_element_ids)

    def _defauts_element(self):
        return {}

    def action_voir_sur_le_plan(self):
        self.ensure_one()
        element = self.floorplan_element_ids[:1]
        if element:
            return element.action_ouvrir_plan()
        return {
            "type": "ir.actions.act_window",
            "name": _("Poser « %s » sur un plan") % self.display_name,
            "res_model": "bf.floorplan.element",
            "views": [[False, "form"]],
            "target": "new",
            "context": {f"default_{k}": v for k, v in self._defauts_element().items()},
        }


class HostingEndpoint(models.Model):
    _name = "hosting.endpoint"
    _inherit = ["hosting.endpoint", "bf.floorplan.sur.le.plan"]

    floorplan_element_ids = fields.One2many(
        "bf.floorplan.element", "endpoint_id", string="Éléments de plan")

    def _defauts_element(self):
        return {"endpoint_id": self.id, "name": self.name,
                "genre": GENRE_PAR_TYPE.get(self.endpoint_type, "autre")}


class HostingServer(models.Model):
    _name = "hosting.server"
    _inherit = ["hosting.server", "bf.floorplan.sur.le.plan"]

    floorplan_element_ids = fields.One2many(
        "bf.floorplan.element", "server_id", string="Éléments de plan")

    def _defauts_element(self):
        return {"server_id": self.id, "name": self.name, "genre": "serveur"}
