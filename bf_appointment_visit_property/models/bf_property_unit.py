"""Le logement gagne un bouton « Faire visiter »."""

from odoo import _, models


class BfPropertyUnit(models.Model):
    _inherit = "bf.property.unit"

    def action_create_visit_listing(self):
        """Ouvrir une inscription à visiter préremplie pour ce logement."""
        self.ensure_one()
        existante = self.env["bf.visit.listing"].search(
            [("unit_id", "=", self.id), ("state", "!=", "closed")], limit=1
        )
        if existante:
            return {
                "type": "ir.actions.act_window",
                "name": _("Inscription à visiter"),
                "res_model": "bf.visit.listing",
                "res_id": existante.id,
                "view_mode": "form",
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Faire visiter ce logement"),
            "res_model": "bf.visit.listing",
            "view_mode": "form",
            "context": {
                "default_unit_id": self.id,
                "default_name": self.display_name,
                "default_listing_kind": "rental",
            },
        }
