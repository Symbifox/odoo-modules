"""Parties communes, générales ou à usage restreint.

Une partie commune à usage restreint (art. 1043 C.c.Q.) sert quelques
fractions plutôt que toutes : balcon, terrasse, stationnement extérieur
attribué. La distinction compte parce que sa charge d'entretien ne se
répartit pas sur l'ensemble des copropriétaires.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfPropertyCommonArea(models.Model):
    _name = "bf.property.common.area"
    _description = "Partie commune"
    _order = "building_id, name"

    name = fields.Char(string="Nom", required=True)
    active = fields.Boolean(default=True)
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        required=True,
        ondelete="cascade",
        index=True,
    )
    syndicat_id = fields.Many2one(
        related="building_id.syndicat_id", store=True, string="Syndicat"
    )
    company_id = fields.Many2one(
        related="building_id.company_id", store=True, string="Société"
    )
    area_type = fields.Selection(
        [
            ("general", "Commune générale"),
            ("restricted", "Commune à usage restreint"),
        ],
        string="Nature",
        default="general",
        required=True,
        help="À usage restreint quand la jouissance est réservée à certaines "
             "fractions (art. 1043 C.c.Q.).",
    )
    restricted_unit_ids = fields.Many2many(
        "bf.property.unit",
        string="Fractions bénéficiaires",
        domain="[('building_id', '=', building_id)]",
        help="Fractions qui ont la jouissance exclusive de cette partie commune.",
    )
    bookable = fields.Boolean(
        string="Réservable",
        help="Espace que les occupants peuvent réserver : salle communautaire, "
             "gym, ascenseur de déménagement. Le moteur de réservation vit dans "
             "un module distinct.",
    )
    area = fields.Float(string="Superficie (m²)", digits=(16, 2))
    description = fields.Text(string="Description")

    @api.constrains("area_type", "restricted_unit_ids")
    def _check_restricted(self):
        for rec in self:
            if rec.area_type == "general" and rec.restricted_unit_ids:
                raise ValidationError(
                    _(
                        "« %s » est déclarée commune générale mais porte des "
                        "fractions bénéficiaires. Passez-la à usage restreint "
                        "ou retirez les fractions."
                    )
                    % rec.name
                )

    @api.constrains("restricted_unit_ids", "building_id")
    def _check_restricted_same_building(self):
        """Le domaine de la vue ne protège que la saisie manuelle.

        Un import ou un appel RPC contourne le domaine, alors la règle est
        rejouée côté serveur : une partie commune ne peut être réservée qu'à
        des fractions de son propre immeuble.
        """
        for rec in self:
            outsiders = rec.restricted_unit_ids.filtered(
                lambda u, r=rec: u.building_id != r.building_id
            )
            if outsiders:
                raise ValidationError(
                    _(
                        "« %(area)s » appartient à l'immeuble %(building)s mais "
                        "réserve sa jouissance à des fractions d'un autre "
                        "immeuble : %(units)s."
                    )
                    % {
                        "area": rec.name,
                        "building": rec.building_id.display_name,
                        "units": ", ".join(outsiders.mapped("display_name")),
                    }
                )
