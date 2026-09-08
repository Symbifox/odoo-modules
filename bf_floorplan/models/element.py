# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .genres import selection_elements, taille_element, libelle_element, ROTATIONS


class BfFloorplanElement(models.Model):
    """Ce qui est posé sur le plan : un poste, un appareil, une prise.

    Chaque élément est un enregistrement sur discussion : il porte ses
    photos, son fil et son historique. Les ponts y accrochent ce qu'ils
    savent d'autre (un appareil du parc, par exemple) par les trois
    crochets `_cible`, `_teinte` et `_infos`.
    """

    _name = "bf.floorplan.element"
    _description = "Élément d'un plan d'étage"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "plan_id, zone_id, genre, name, id"

    plan_id = fields.Many2one("bf.floorplan", string="Plan", required=True,
                              ondelete="cascade", index=True)
    company_id = fields.Many2one(related="plan_id.company_id", store=True)
    name = fields.Char(string="Étiquette", tracking=True,
                       help="Ce qui s'écrit sur le plan : « P-12 », « Imprimante RH »…")
    genre = fields.Selection(selection_elements(), string="Nature", required=True,
                             default="poste", tracking=True)
    x = fields.Float(string="X (cm)", default=0.0)
    y = fields.Float(string="Y (cm)", default=0.0)
    w = fields.Float(string="Largeur (cm)")
    h = fields.Float(string="Profondeur (cm)")
    rotation = fields.Selection(ROTATIONS, string="Rotation", default="0",
                                required=True)
    zone_id = fields.Many2one(
        "bf.floorplan.zone", string="Zone", compute="_compute_zone_id", store=True,
        help="Déduite de la position : la zone qui contient le centre de "
             "l'élément, la plus petite s'il y en a plusieurs.")
    employee_id = fields.Many2one("hr.employee", string="Occupant", tracking=True,
                                  help="Qui travaille là.")
    active = fields.Boolean(default=True)
    notes = fields.Text(string="Notes")
    lien_count = fields.Integer(compute="_compute_lien_count", string="Liens")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            genre = vals.get("genre") or "poste"
            w, h = taille_element(genre)
            vals.setdefault("w", w)
            vals.setdefault("h", h)
            if not vals.get("w"):
                vals["w"] = w
            if not vals.get("h"):
                vals["h"] = h
        return super().create(vals_list)

    @api.onchange("genre")
    def _onchange_genre(self):
        for el in self:
            if el.genre:
                el.w, el.h = taille_element(el.genre)

    @api.depends("name", "genre", "plan_id")
    def _compute_display_name(self):
        for el in self:
            el.display_name = el.name or libelle_element(el.genre)

    @api.depends("x", "y", "w", "h", "plan_id",
                 "plan_id.zone_ids.x", "plan_id.zone_ids.y",
                 "plan_id.zone_ids.w", "plan_id.zone_ids.h")
    def _compute_zone_id(self):
        for el in self:
            cx, cy = el._centre()
            dedans = el.plan_id.zone_ids.filtered(lambda z: z._contient(cx, cy))
            el.zone_id = dedans.sorted(lambda z: z._aire())[:1] if dedans else False

    def _compute_lien_count(self):
        Lien = self.env["bf.floorplan.lien"]
        for el in self:
            el.lien_count = Lien.search_count(
                ["|", ("src_id", "=", el.id), ("dst_id", "=", el.id)])

    @api.constrains("x", "y", "w", "h", "plan_id")
    def _check_geometrie(self):
        for el in self:
            if el.w <= 0 or el.h <= 0:
                raise ValidationError(_("« %s » a une largeur et une profondeur "
                                        "positives.") % el.display_name)
            plan = el.plan_id
            if (el.x < 0 or el.y < 0 or el.x + el.w > plan.largeur + 1e-6
                    or el.y + el.h > plan.profondeur + 1e-6):
                raise ValidationError(_(
                    "« %(el)s » sort du plan (%(l)s × %(p)s cm).")
                    % {"el": el.display_name, "l": int(plan.largeur),
                       "p": int(plan.profondeur)})

    def _centre(self):
        self.ensure_one()
        return self.x + self.w / 2.0, self.y + self.h / 2.0

    # --- crochets pour les ponts --------------------------------------------

    def _cible(self):
        """L'enregistrement que la forme représente, s'il y en a un.

        `False`, ou `{"modele", "id", "nom"}`. Le module seul n'en connaît
        aucun ; un pont en pose. Le clic sur la forme ouvre la cible plutôt
        que l'élément, parce que c'est elle qu'on est venu chercher.
        """
        self.ensure_one()
        return False

    def _teinte(self):
        """Ce que le plan signale : '', 'alerte', 'attention' ou 'ok'."""
        self.ensure_one()
        return ""

    def _infos(self):
        """Les phrases de l'infobulle, dans l'ordre."""
        self.ensure_one()
        infos = [libelle_element(self.genre)]
        if self.employee_id:
            infos.append(self.employee_id.name)
        if self.zone_id:
            infos.append(self.zone_id.name)
        return infos

    def action_tourner(self):
        for el in self:
            el.rotation = str((int(el.rotation) + 90) % 360)

    def action_ouvrir_plan(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.floorplan",
            "res_id": self.plan_id.id,
            "views": [[False, "form"]],
            "target": "current",
            "context": {"bf_floorplan_surligne": self.id},
        }
