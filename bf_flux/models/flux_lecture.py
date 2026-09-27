# -*- coding: utf-8 -*-
"""Ce qu'une personne a lu. L'état est à elle : ce que l'un a lu reste à lire
pour les autres membres de la même liste."""
from odoo import api, fields, models


class FluxLecture(models.Model):
    _name = "bf.flux.lecture"
    _description = "Élément de flux lu"
    _order = "date desc"

    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, ondelete="cascade",
        index=True, default=lambda s: s.env.user)
    element_id = fields.Many2one(
        "bf.flux.element", string="Élément", required=True, ondelete="cascade", index=True)
    date = fields.Datetime("Lu le", default=fields.Datetime.now, required=True)

    _sql_constraints = [
        ("user_element_unique", "UNIQUE(user_id, element_id)",
         "Un élément n'est lu qu'une fois par personne."),
    ]

    @api.model
    def _flux_marquer(self, elements, user):
        """Marque lus les éléments pour la personne ; rend ce qui était nouveau."""
        deja = set(self.sudo().search([
            ("user_id", "=", user.id), ("element_id", "in", elements.ids)]).element_id.ids)
        nouveaux = elements.filtered(lambda e: e.id not in deja)
        self.sudo().create([{"user_id": user.id, "element_id": e.id} for e in nouveaux])
        return nouveaux
