# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    floorplan_element_ids = fields.One2many(
        "bf.floorplan.element", "employee_id", string="Postes sur un plan")
    floorplan_element_count = fields.Integer(
        compute="_compute_floorplan_element_count", string="Sur le plan")

    @api.depends("floorplan_element_ids")
    def _compute_floorplan_element_count(self):
        for emp in self:
            emp.floorplan_element_count = len(emp.floorplan_element_ids)

    def action_voir_sur_le_plan(self):
        """Le poste de la personne, surligné sur son plan.

        Deux postes sur deux plans : la liste, pour choisir.
        """
        self.ensure_one()
        elements = self.floorplan_element_ids
        if len(elements) == 1:
            return elements.action_ouvrir_plan()
        return {
            "type": "ir.actions.act_window",
            "name": _("Postes de %s") % self.name,
            "res_model": "bf.floorplan.element",
            "view_mode": "list,form",
            "domain": [("employee_id", "=", self.id)],
        }


class HrEmployeePublic(models.Model):
    """Le profil public, celui que lit qui n'a pas les droits RH : le bouton
    y est aussi, sinon la moitié du bureau ne trouverait personne."""

    _inherit = "hr.employee.public"

    floorplan_element_count = fields.Integer(
        compute="_compute_floorplan_element_count", string="Sur le plan")

    def _compute_floorplan_element_count(self):
        Element = self.env["bf.floorplan.element"]
        for emp in self:
            emp.floorplan_element_count = Element.search_count(
                [("employee_id", "=", emp.id)])

    def action_voir_sur_le_plan(self):
        self.ensure_one()
        elements = self.env["bf.floorplan.element"].search([("employee_id", "=", self.id)])
        if len(elements) == 1:
            return elements.action_ouvrir_plan()
        return {
            "type": "ir.actions.act_window",
            "name": _("Postes de %s") % self.name,
            "res_model": "bf.floorplan.element",
            "view_mode": "list,form",
            "domain": [("employee_id", "=", self.id)],
        }
