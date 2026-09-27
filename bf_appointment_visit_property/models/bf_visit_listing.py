"""L'inscription peut désigner un logement du parc.

Le pont ne déplace rien : l'inscription garde ses propres champs d'adresse et
d'occupation, et le logement ne fait que les remplir. Un gestionnaire qui change
d'avis sur une visite ne doit pas avoir à modifier sa fiche de logement pour ça.
"""

from odoo import _, api, fields, models


class BfVisitListing(models.Model):
    _inherit = "bf.visit.listing"

    unit_id = fields.Many2one(
        "bf.property.unit", string="Logement", index=True,
        help="Le logement du parc qu'on fait visiter. Son adresse et son "
             "occupant descendent d'ici.",
    )
    building_id = fields.Many2one(
        related="unit_id.building_id", string="Immeuble", store=True, readonly=True,
    )

    @api.onchange("unit_id")
    def _onchange_unit_id(self):
        """Reprendre ce que le parc sait déjà, sans écraser ce qui est saisi."""
        for rec in self:
            unite = rec.unit_id
            if not unite:
                continue
            immeuble = unite.building_id
            if not rec.name:
                rec.name = unite.display_name
            rec.street = immeuble.street or rec.street
            rec.city = immeuble.city or rec.city
            rec.zip = immeuble.zip or rec.zip
            rec.state_id = immeuble.state_id or rec.state_id
            rec.country_id = immeuble.country_id or rec.country_id
            if unite.is_rented and unite.occupant_id:
                rec.occupancy = "tenant"
                rec.tenant_id = unite.occupant_id
                # Le plancher légal se pose tout de suite : sans lui, une
                # première plage saisie dans la foulée serait refusée sans
                # qu'on comprenne pourquoi.
                if rec.lead_time_hours < 24.0:
                    rec.lead_time_hours = 24.0
            elif unite.is_rented:
                rec.occupancy = "tenant"
            else:
                rec.occupancy = "owner"
