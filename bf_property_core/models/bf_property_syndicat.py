"""Syndicat de copropriété.

La personne morale créée de plein droit par la publication de la déclaration
de copropriété (art. 1039 C.c.Q.). Porte la base de quotes-parts contre
laquelle le total des fractions est vérifié.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare

QUOTE_PART_DIGITS = 4


class BfPropertySyndicat(models.Model):
    _name = "bf.property.syndicat"
    _description = "Syndicat de copropriété"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(string="Nom", required=True, tracking=True)
    active = fields.Boolean(default=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Personne morale",
        tracking=True,
        help="Fiche du syndicat comme personne morale distincte des "
             "copropriétaires. Sert de tiers pour les contrats et la facturation.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        default=lambda self: self.env.company,
        required=True,
    )
    neq = fields.Char(
        string="NEQ",
        tracking=True,
        help="Numéro d'entreprise du Québec du syndicat.",
    )
    declaration_date = fields.Date(
        string="Date de la déclaration",
        tracking=True,
        help="Date de publication de la déclaration de copropriété au registre "
             "foncier.",
    )
    fraction_base = fields.Integer(
        string="Base des quotes-parts",
        default=1000,
        required=True,
        tracking=True,
        help="Total que doivent atteindre les quotes-parts de toutes les "
             "fractions. 1000 pour des millièmes, 10000 pour des dix-millièmes.",
    )

    building_ids = fields.One2many(
        "bf.property.building", "syndicat_id", string="Immeubles"
    )
    unit_ids = fields.One2many(
        "bf.property.unit", "syndicat_id", string="Fractions"
    )
    building_count = fields.Integer(compute="_compute_counts")
    unit_count = fields.Integer(compute="_compute_counts")

    quote_part_total = fields.Float(
        string="Total des quotes-parts",
        compute="_compute_quote_part",
        store=True,
        digits=(16, 4),
    )
    quote_part_gap = fields.Float(
        string="Écart",
        compute="_compute_quote_part",
        store=True,
        digits=(16, 4),
        help="Total des quotes-parts moins la base déclarée. Zéro quand la "
             "répartition est complète.",
    )
    quote_part_state = fields.Selection(
        [
            ("empty", "Aucune fraction"),
            ("under", "Incomplet"),
            ("balanced", "Équilibré"),
            ("over", "Dépassement"),
        ],
        string="État des quotes-parts",
        compute="_compute_quote_part",
        store=True,
    )

    _sql_constraints = [
        (
            "fraction_base_positive",
            "CHECK(fraction_base > 0)",
            "La base des quotes-parts doit être supérieure à zéro.",
        ),
    ]

    @api.depends("building_ids", "building_ids.active", "unit_ids", "unit_ids.active")
    def _compute_counts(self):
        for syndicat in self:
            syndicat.building_count = len(syndicat.building_ids.filtered("active"))
            syndicat.unit_count = len(syndicat.unit_ids.filtered("active"))

    @api.depends("unit_ids.quote_part", "unit_ids.active", "fraction_base")
    def _compute_quote_part(self):
        # `active` doit figurer aux dépendances ET au filtre : sans la
        # dépendance le total ne se recalcule pas quand on archive une
        # fraction, et sans le filtre un appel fait avec active_test=False
        # recompterait les fractions archivées.
        for syndicat in self:
            total = sum(syndicat.unit_ids.filtered("active").mapped("quote_part"))
            syndicat.quote_part_total = total
            syndicat.quote_part_gap = total - syndicat.fraction_base
            comparison = float_compare(
                total, syndicat.fraction_base, precision_digits=QUOTE_PART_DIGITS
            )
            if not syndicat.unit_ids.filtered("active"):
                syndicat.quote_part_state = "empty"
            elif comparison == 0:
                syndicat.quote_part_state = "balanced"
            elif comparison < 0:
                syndicat.quote_part_state = "under"
            else:
                syndicat.quote_part_state = "over"

    @api.constrains("fraction_base")
    def _check_fraction_base(self):
        for syndicat in self:
            if syndicat.fraction_base <= 0:
                raise ValidationError(
                    _("La base des quotes-parts doit être supérieure à zéro.")
                )

    def action_view_units(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Fractions"),
            "res_model": "bf.property.unit",
            "view_mode": "list,form",
            "domain": [("syndicat_id", "=", self.id)],
            "context": {"default_syndicat_id": self.id},
        }

    def action_view_buildings(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Immeubles"),
            "res_model": "bf.property.building",
            "view_mode": "list,form",
            "domain": [("syndicat_id", "=", self.id)],
            "context": {"default_syndicat_id": self.id},
        }
