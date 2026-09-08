# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .genres import selection_zones, couleur_zone


class BfFloorplanZone(models.Model):
    """Une salle, un bureau, une aire : un rectangle nommé sur le plan."""

    _name = "bf.floorplan.zone"
    _description = "Zone d'un plan d'étage"
    _inherit = ["mail.thread"]
    _order = "plan_id, sequence, id"

    plan_id = fields.Many2one("bf.floorplan", string="Plan", required=True,
                              ondelete="cascade", index=True)
    company_id = fields.Many2one(related="plan_id.company_id", store=True)
    sequence = fields.Integer(string="Ordre", default=10)
    name = fields.Char(string="Zone", required=True, tracking=True)
    code = fields.Char(string="Code", help="Ex. : « B-204 », « SR-1 ».")
    genre = fields.Selection(selection_zones(), string="Nature", required=True,
                             default="bureau", tracking=True)
    x = fields.Float(string="X (cm)", default=0.0)
    y = fields.Float(string="Y (cm)", default=0.0)
    w = fields.Float(string="Largeur (cm)", default=400.0)
    h = fields.Float(string="Profondeur (cm)", default=300.0)
    capacite = fields.Integer(string="Places", default=0,
                              help="Combien de personnes la zone accueille.")
    occupes = fields.Integer(compute="_compute_occupes", string="Occupés",
                             help="Postes de travail de la zone auxquels une "
                                  "personne est affectée.")
    element_ids = fields.One2many("bf.floorplan.element", "zone_id", string="Éléments")
    element_count = fields.Integer(compute="_compute_element_count", string="Éléments")
    notes = fields.Text(string="Notes")
    couleur = fields.Char(compute="_compute_couleur", string="Couleur")

    @api.depends("element_ids.genre", "element_ids.employee_id")
    def _compute_occupes(self):
        for zone in self:
            zone.occupes = len(zone.element_ids.filtered(
                lambda e: e.genre == "poste" and e.employee_id))

    @api.depends("element_ids")
    def _compute_element_count(self):
        for zone in self:
            zone.element_count = len(zone.element_ids)

    @api.depends("genre")
    def _compute_couleur(self):
        for zone in self:
            zone.couleur = couleur_zone(zone.genre)

    @api.constrains("x", "y", "w", "h", "plan_id")
    def _check_geometrie(self):
        for zone in self:
            if zone.w <= 0 or zone.h <= 0:
                raise ValidationError(_("La zone « %s » a une largeur et une "
                                        "profondeur positives.") % zone.name)
            plan = zone.plan_id
            if (zone.x < 0 or zone.y < 0 or zone.x + zone.w > plan.largeur + 1e-6
                    or zone.y + zone.h > plan.profondeur + 1e-6):
                raise ValidationError(_(
                    "La zone « %(zone)s » sort du plan (%(l)s × %(p)s cm).")
                    % {"zone": zone.name, "l": int(plan.largeur),
                       "p": int(plan.profondeur)})

    @api.constrains("capacite")
    def _check_capacite(self):
        for zone in self:
            if zone.capacite < 0:
                raise ValidationError(_("La capacité n'est pas négative."))

    def _contient(self, cx, cy):
        self.ensure_one()
        return self.x <= cx <= self.x + self.w and self.y <= cy <= self.y + self.h

    def _aire(self):
        self.ensure_one()
        return self.w * self.h
