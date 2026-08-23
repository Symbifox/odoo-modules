"""Fraction : partie privative et sa quote-part des parties communes."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfPropertyUnit(models.Model):
    _name = "bf.property.unit"
    _description = "Fraction"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "building_id, name"
    _rec_names_search = ["name", "building_id.name"]

    name = fields.Char(
        string="Numéro",
        required=True,
        tracking=True,
        help="Numéro de la fraction tel qu'il apparaît à la déclaration de "
             "copropriété. Souvent le numéro d'appartement.",
    )
    active = fields.Boolean(default=True)
    building_id = fields.Many2one(
        "bf.property.building",
        string="Immeuble",
        required=True,
        ondelete="cascade",
        tracking=True,
        index=True,
    )
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        related="building_id.syndicat_id",
        store=True,
        index=True,
    )
    company_id = fields.Many2one(
        related="building_id.company_id", store=True, string="Société"
    )

    unit_type = fields.Selection(
        [
            ("residential", "Résidentiel"),
            ("parking", "Stationnement"),
            ("storage", "Rangement"),
            ("commercial", "Commercial"),
            ("other", "Autre"),
        ],
        string="Type",
        default="residential",
        required=True,
        tracking=True,
    )
    quote_part = fields.Float(
        string="Quote-part",
        digits=(16, 4),
        tracking=True,
        help="Valeur relative de la fraction, dans la base déclarée par le "
             "syndicat. Détermine la part des charges communes et le poids du "
             "vote en assemblée.",
    )
    quote_part_pct = fields.Float(
        string="Quote-part (%)",
        compute="_compute_quote_part_pct",
        digits=(16, 4),
    )
    floor = fields.Integer(string="Étage")
    area = fields.Float(string="Superficie (m²)", digits=(16, 2))

    ownership_ids = fields.One2many(
        "bf.property.ownership", "unit_id", string="Historique de propriété"
    )
    owner_ids = fields.Many2many(
        "res.partner",
        string="Copropriétaires",
        compute="_compute_owners",
        store=True,
        help="Copropriétaires actuels, calculés à partir de l'historique de "
             "propriété.",
    )
    owner_display = fields.Char(
        string="Propriétaire", compute="_compute_owners", store=True
    )

    is_rented = fields.Boolean(
        string="Louée",
        tracking=True,
        help="Cochée quand la fraction est occupée par un locataire plutôt que "
             "par son propriétaire. Le syndicat doit tenir au registre le nom "
             "et l'adresse de chaque locataire (art. 1070 C.c.Q.).",
    )
    occupant_id = fields.Many2one(
        "res.partner",
        string="Occupant",
        tracking=True,
        help="Occupant réel lorsqu'il diffère du propriétaire.",
    )

    _sql_constraints = [
        (
            "quote_part_positive",
            "CHECK(quote_part >= 0)",
            "Une quote-part ne peut pas être négative.",
        ),
    ]

    def init(self):
        """Unicité du numéro de fraction, restreinte aux fractions vivantes.

        Une contrainte UNIQUE ordinaire compterait aussi les archivées, ce qui
        rendrait impossible la correction d'une erreur de saisie par archivage
        puis ressaisie du même numéro. L'index partiel ignore les archivées.
        """
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS bf_property_unit_active_name_uniq
            ON bf_property_unit (building_id, name)
            WHERE active
            """
        )

    def write(self, vals):
        # Décocher « louée » vide l'occupant plutôt que de refuser l'écriture :
        # sinon la contrainte oblige à deux écritures successives pour une
        # opération qui n'en est qu'une du point de vue de l'utilisateur.
        if "is_rented" in vals and not vals["is_rented"] and "occupant_id" not in vals:
            vals = dict(vals, occupant_id=False)
        return super().write(vals)

    @api.depends("quote_part", "syndicat_id.fraction_base")
    def _compute_quote_part_pct(self):
        for unit in self:
            base = unit.syndicat_id.fraction_base or 0
            unit.quote_part_pct = (unit.quote_part / base * 100.0) if base else 0.0

    @api.depends(
        "ownership_ids.partner_id",
        "ownership_ids.date_start",
        "ownership_ids.date_end",
    )
    def _compute_owners(self):
        today = fields.Date.context_today(self)
        for unit in self:
            current = unit.ownership_ids.filtered(
                lambda o: (not o.date_start or o.date_start <= today)
                and (not o.date_end or o.date_end >= today)
            )
            partners = current.mapped("partner_id")
            unit.owner_ids = [(6, 0, partners.ids)]
            unit.owner_display = ", ".join(partners.mapped("name")) or False

    @api.constrains("occupant_id", "is_rented")
    def _check_occupant(self):
        for unit in self:
            if unit.occupant_id and not unit.is_rented:
                raise ValidationError(
                    _(
                        "La fraction %s porte un occupant distinct sans être "
                        "marquée comme louée. Cochez « Louée » ou retirez "
                        "l'occupant."
                    )
                    % unit.display_name
                )

    @api.depends("name", "building_id.name")
    def _compute_display_name(self):
        for unit in self:
            if unit.building_id:
                unit.display_name = f"{unit.building_id.name} — {unit.name}"
            else:
                unit.display_name = unit.name or ""
