# Part of bf_music_licensing. Voir LICENSE.
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

SQFT_PER_SQM = 10.7639104167


class MusicEstablishment(models.Model):
    """Le lieu où la musique joue, et l'assiette des redevances qu'il doit."""

    _name = "bf.music.establishment"
    _description = "Establishment playing music"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "partner_id, name"

    name = fields.Char(string="Name", required=True, tracking=True)
    partner_id = fields.Many2one(
        "res.partner", string="Client", required=True, tracking=True,
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )

    area_value = fields.Float(string="Area", tracking=True)
    area_uom = fields.Selection(
        selection=[("sqm", "m²"), ("sqft", "ft²")],
        string="Unit", default="sqm", required=True,
    )
    area_sqm = fields.Float(
        string="Area (m²)", compute="_compute_area", store=True, digits=(16, 2),
    )
    area_sqft = fields.Float(
        string="Area (ft²)", compute="_compute_area", store=True, digits=(16, 2),
    )

    usage_background = fields.Boolean(
        string="Background music", default=True, tracking=True,
    )
    usage_on_hold = fields.Boolean(
        string="Music on hold", tracking=True,
    )
    on_hold_lines = fields.Integer(string="Phone lines", default=0)
    days_of_operation = fields.Integer(
        string="Operating days per year", default=365,
        help="Number of days on which music is played. Ré:Sonne Tariff "
             "3.B multiplies the area by this number.",
    )
    seasonal = fields.Boolean(
        string="Seasonal establishment",
        help="Operated less than six months a year. Some tariffs then "
             "provide for half the rate.",
    )

    music_source = fields.Selection(
        selection=[
            ("radio", "Commercial radio"),
            ("consumer_streaming", "Consumer streaming service"),
            ("business_streaming", "Business streaming service"),
            ("supplier", "Background music supplier"),
            ("own", "Establishment's own recordings"),
            ("live", "Live music"),
            ("unknown", "Unknown"),
        ],
        string="Music source",
        default="unknown",
        required=True,
        tracking=True,
    )
    source_warning = fields.Char(
        string="Music source finding", compute="_compute_source_warning",
    )
    entandem_account = fields.Char(string="Entandem account", tracking=True)

    licence_ids = fields.One2many(
        "bf.music.licence", "establishment_id", string="Licence periods",
    )
    licence_count = fields.Integer(compute="_compute_totals", store=True)
    amount_reference = fields.Monetary(
        string="Reference royalties", compute="_compute_totals", store=True,
        currency_field="currency_id",
    )
    amount_at_risk = fields.Monetary(
        string="Paid under uncertified tariffs", compute="_compute_totals", store=True,
        currency_field="currency_id",
        help="What the establishment paid for periods the Board has not "
             "yet certified. This amount remains subject to revision.",
    )
    adjustment_total = fields.Monetary(
        string="Recorded adjustment", compute="_compute_totals", store=True,
        currency_field="currency_id",
        help="The actual difference between what was paid and what the "
             "certified tariff requires, for periods already certified.",
    )
    uncertified_period_count = fields.Integer(
        string="Uncertified periods", compute="_compute_totals", store=True,
    )

    @api.depends("area_value", "area_uom")
    def _compute_area(self):
        for rec in self:
            if rec.area_uom == "sqft":
                rec.area_sqft = rec.area_value
                rec.area_sqm = rec.area_value / SQFT_PER_SQM if rec.area_value else 0.0
            else:
                rec.area_sqm = rec.area_value
                rec.area_sqft = rec.area_value * SQFT_PER_SQM

    @api.depends("music_source")
    def _compute_source_warning(self):
        for rec in self:
            if rec.music_source == "consumer_streaming":
                rec.source_warning = _(
                    "A consumer streaming service used in a business adds "
                    "to the royalties: its terms of use restrict "
                    "listening to personal use. Check the service "
                    "agreement before making a written finding."
                )
            elif rec.music_source == "supplier":
                rec.source_warning = _(
                    "A background music supplier normally holds the "
                    "supply licences. Check what the subscription covers "
                    "before billing a duplicate royalty."
                )
            elif rec.music_source == "unknown":
                rec.source_warning = _(
                    "Music source not established. It is the first "
                    "question of the assessment: without it, no "
                    "conclusion holds."
                )
            else:
                rec.source_warning = False

    @api.depends(
        "licence_ids.amount_reference",
        "licence_ids.amount_at_risk",
        "licence_ids.adjustment_total",
        "licence_ids.has_uncertified",
    )
    def _compute_totals(self):
        for rec in self:
            rec.licence_count = len(rec.licence_ids)
            rec.amount_reference = sum(rec.licence_ids.mapped("amount_reference"))
            rec.amount_at_risk = sum(rec.licence_ids.mapped("amount_at_risk"))
            rec.adjustment_total = sum(rec.licence_ids.mapped("adjustment_total"))
            rec.uncertified_period_count = len(
                rec.licence_ids.filtered("has_uncertified")
            )

    def _usages(self):
        self.ensure_one()
        usages = set()
        if self.usage_background:
            usages.add("background")
        if self.usage_on_hold:
            usages.add("on_hold")
        return usages

    def action_build_history(self, year_from=2020, year_to=None):
        """Crée une période de licence par année manquante, lignes comprises.

        C'est le geste qui produit le chiffre d'exposition : une année civile
        par ligne, chacune évaluée au tarif de sa période.
        """
        year_to = year_to or date.today().year
        if year_from > year_to:
            raise UserError(_("The start year comes after the end year."))
        created = self.env["bf.music.licence"]
        for rec in self:
            if not rec._usages():
                raise UserError(
                    _("No use is checked on %s: nothing to assess.", rec.name)
                )
            # `mapped(callable)` applique la fonction au RECORDSET entier en
            # Odoo 18, pas ligne par ligne : la compréhension est le seul
            # moyen d'obtenir une valeur par période.
            existing = {lic.date_start.year for lic in rec.licence_ids}
            for year in range(year_from, year_to + 1):
                if year in existing:
                    continue
                licence = self.env["bf.music.licence"].create({
                    "establishment_id": rec.id,
                    "date_start": date(year, 1, 1),
                    "date_end": date(year, 12, 31),
                    # Les trois tarifs relevés fixent le paiement au plus tard
                    # le 31 janvier de l'année visée.
                    "due_date": date(year, 1, 31),
                    "entandem_account": rec.entandem_account,
                })
                licence.action_generate_lines()
                created |= licence
        return created

    def action_open_licences(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Licence periods"),
            "res_model": "bf.music.licence",
            "view_mode": "list,form",
            "domain": [("establishment_id", "=", self.id)],
            "context": {"default_establishment_id": self.id},
        }
