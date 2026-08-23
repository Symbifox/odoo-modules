"""Immeuble détenu en copropriété divise."""
from odoo import _, api, fields, models


class BfPropertyBuilding(models.Model):
    _name = "bf.property.building"
    _description = "Immeuble"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "syndicat_id, name"

    name = fields.Char(string="Nom", required=True, tracking=True)
    active = fields.Boolean(default=True)
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        related="syndicat_id.company_id", store=True, string="Société"
    )

    street = fields.Char(string="Rue")
    street2 = fields.Char(string="Rue 2")
    city = fields.Char(string="Ville")
    state_id = fields.Many2one(
        "res.country.state",
        string="Province",
        domain="[('country_id', '=', country_id)]",
    )
    zip = fields.Char(string="Code postal")
    country_id = fields.Many2one(
        "res.country",
        string="Pays",
        default=lambda self: self.env.ref("base.ca", raise_if_not_found=False),
    )

    cadastre = fields.Char(
        string="Lot cadastral",
        help="Numéro de lot au cadastre du Québec.",
    )
    year_built = fields.Integer(string="Année de construction")
    storeys = fields.Integer(string="Nombre d'étages")

    unit_ids = fields.One2many("bf.property.unit", "building_id", string="Fractions")
    common_area_ids = fields.One2many(
        "bf.property.common.area", "building_id", string="Parties communes"
    )
    unit_count = fields.Integer(compute="_compute_counts")
    common_area_count = fields.Integer(compute="_compute_counts")
    quote_part_total = fields.Float(
        string="Total des quotes-parts",
        compute="_compute_quote_part_total",
        store=True,
        digits=(16, 4),
        help="Somme des quotes-parts des fractions de cet immeuble. Un syndicat "
             "peut regrouper plusieurs immeubles : c'est le total du syndicat "
             "qui doit atteindre la base, pas celui d'un immeuble isolé.",
    )

    @api.depends(
        "unit_ids", "unit_ids.active", "common_area_ids", "common_area_ids.active"
    )
    def _compute_counts(self):
        for building in self:
            building.unit_count = len(building.unit_ids.filtered("active"))
            building.common_area_count = len(
                building.common_area_ids.filtered("active")
            )

    @api.depends("unit_ids.quote_part", "unit_ids.active")
    def _compute_quote_part_total(self):
        for building in self:
            building.quote_part_total = sum(
                building.unit_ids.filtered("active").mapped("quote_part")
            )

    def action_view_units(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Fractions"),
            "res_model": "bf.property.unit",
            "view_mode": "list,form",
            "domain": [("building_id", "=", self.id)],
            "context": {
                "default_building_id": self.id,
                "default_syndicat_id": self.syndicat_id.id,
            },
        }
