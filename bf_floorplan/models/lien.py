# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .genres import GENRES_LIEN


class BfFloorplanLien(models.Model):
    """Un câble entre deux éléments du même plan."""

    _name = "bf.floorplan.lien"
    _description = "Lien entre deux éléments d'un plan"
    _order = "plan_id, id"

    plan_id = fields.Many2one("bf.floorplan", string="Plan", required=True,
                              ondelete="cascade", index=True)
    company_id = fields.Many2one(related="plan_id.company_id", store=True)
    src_id = fields.Many2one("bf.floorplan.element", string="De", required=True,
                             ondelete="cascade")
    dst_id = fields.Many2one("bf.floorplan.element", string="Vers", required=True,
                             ondelete="cascade")
    genre = fields.Selection(GENRES_LIEN, string="Nature", required=True,
                             default="reseau")
    name = fields.Char(string="Étiquette", help="Ex. : « A-12 », « Cat6 », « 20 A ».")

    @api.constrains("src_id", "dst_id", "plan_id")
    def _check_bouts(self):
        for lien in self:
            if lien.src_id == lien.dst_id:
                raise ValidationError(_("Un lien relie deux éléments différents."))
            if lien.src_id.plan_id != lien.plan_id or lien.dst_id.plan_id != lien.plan_id:
                raise ValidationError(_("Un lien relie deux éléments du même plan."))
            jumeau = self.search([
                ("id", "!=", lien.id), ("plan_id", "=", lien.plan_id.id),
                "|",
                "&", ("src_id", "=", lien.src_id.id), ("dst_id", "=", lien.dst_id.id),
                "&", ("src_id", "=", lien.dst_id.id), ("dst_id", "=", lien.src_id.id),
            ], limit=1)
            if jumeau:
                raise ValidationError(_("« %(a)s » et « %(b)s » sont déjà reliés.")
                                      % {"a": lien.src_id.display_name,
                                         "b": lien.dst_id.display_name})

    @api.depends("src_id", "dst_id", "name")
    def _compute_display_name(self):
        for lien in self:
            base = f"{lien.src_id.display_name} — {lien.dst_id.display_name}"
            lien.display_name = f"{base} ({lien.name})" if lien.name else base
