# Part of bf_music_licensing. Voir LICENSE.
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

SQFT_PER_SQM = 10.7639104167


class MusicTariff(models.Model):
    """Un tarif de redevance, pour une société de gestion, un usage et une période.

    La règle de conception du module tient dans ce modèle : une période porte
    UNE ligne, qui connaît à la fois le taux **proposé** et le taux
    **homologué**. La Commission du droit d'auteur homologue des années après
    coup, avec effet rétroactif ; un référentiel qui n'aurait que le taux en
    vigueur ne saurait jamais dire de combien la facture a bougé.
    """

    _name = "bf.music.tariff"
    _description = "Music royalty tariff"
    _inherit = ["mail.thread"]
    _order = "society, code, date_start desc"

    name = fields.Char(compute="_compute_name", store=True)
    society = fields.Selection(
        selection=[("socan", "SOCAN"), ("resound", "Ré:Sonne")],
        string="Collective society",
        required=True,
        tracking=True,
    )
    code = fields.Char(
        string="Tariff",
        required=True,
        tracking=True,
        help="The number as the Board designates it: 15.A, 15.B, 3.B.",
    )
    label = fields.Char(string="Title", required=True)
    usage = fields.Selection(
        selection=[
            ("background", "Background music"),
            ("on_hold", "Music on hold"),
        ],
        string="Intended use",
        required=True,
        default="background",
    )
    date_start = fields.Date(string="Period start", required=True, tracking=True)
    date_end = fields.Date(string="Period end", required=True, tracking=True)
    basis = fields.Selection(
        selection=[
            ("area_sqm", "Per square metre"),
            ("area_sqft", "Per square foot"),
            ("area_sqm_day", "Per square metre per operating day"),
            ("area_sqft_day", "Per square foot per operating day"),
            ("line", "Per phone line"),
            ("flat", "Flat amount"),
        ],
        string="Basis",
        required=True,
        default="area_sqm",
    )
    state = fields.Selection(
        selection=[("proposed", "Proposed"), ("certified", "Certified")],
        string="State",
        required=True,
        default="proposed",
        tracking=True,
        help="Proposed: published by the Board but not yet certified. "
             "What the client pays under a proposed tariff remains "
             "subject to revision.",
    )
    rate_proposed = fields.Float(
        string="Proposed rate", digits=(16, 5), tracking=True,
        help="Rate per basis unit. On a per-phone-line basis, it applies "
             "only to ADDITIONAL lines: the first one is billed at the "
             "base amount.",
    )
    rate_base_proposed = fields.Monetary(
        string="Proposed base amount", currency_field="currency_id",
        help="Amount due for the first unit, when the tariff is worded "
             "\"so much for the first, so much for each additional unit\".",
    )
    minimum_proposed = fields.Monetary(
        string="Proposed minimum", currency_field="currency_id",
        help="Minimum annual royalty, when the tariff provides for one.",
    )
    rate_certified = fields.Float(
        string="Certified rate", digits=(16, 5), tracking=True,
    )
    rate_base_certified = fields.Monetary(
        string="Certified base amount", currency_field="currency_id",
    )
    minimum_certified = fields.Monetary(
        string="Certified minimum", currency_field="currency_id",
    )
    certification_date = fields.Date(string="Certification date", tracking=True)
    publication_date = fields.Date(string="Proposed tariff publication date")
    source_url = fields.Char(string="Source")
    rate_confirmed = fields.Boolean(
        string="Rate verified at source",
        default=False,
        help="False until the rate has been read in the text of the "
             "tariff itself. An unverified rate must not be used as a "
             "commercial promise.",
    )
    seasonal_half = fields.Boolean(
        string="Seasonal half rate",
        help="The tariff provides for half the rate for an establishment "
             "operated less than six months a year.",
    )
    notes = fields.Text(string="Notes")
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)

    rate_in_force = fields.Float(
        string="Rate in force", compute="_compute_in_force", digits=(16, 5),
    )
    rate_base_in_force = fields.Monetary(
        string="Base amount in force", compute="_compute_in_force",
        currency_field="currency_id",
    )
    minimum_in_force = fields.Monetary(
        string="Minimum in force", compute="_compute_in_force",
        currency_field="currency_id",
    )

    _sql_constraints = [
        (
            "period_unique",
            "unique(society, code, usage, date_start, company_id)",
            "A period can carry only one line per tariff: the proposed "
            "rate and the certified rate live on the same line.",
        ),
    ]

    @api.depends("society", "code", "date_start", "date_end")
    def _compute_name(self):
        labels = dict(self._fields["society"].selection)
        for rec in self:
            years = ""
            if rec.date_start and rec.date_end:
                years = f" {rec.date_start.year}-{rec.date_end.year}"
            rec.name = f"{labels.get(rec.society, '')} {rec.code or ''}{years}".strip()

    @api.depends("state", "rate_proposed", "rate_certified",
                 "minimum_proposed", "minimum_certified",
                 "rate_base_proposed", "rate_base_certified")
    def _compute_in_force(self):
        for rec in self:
            certified = rec.state == "certified"
            rec.rate_in_force = rec.rate_certified if certified else rec.rate_proposed
            rec.minimum_in_force = (
                rec.minimum_certified if certified else rec.minimum_proposed
            )
            rec.rate_base_in_force = (
                rec.rate_base_certified if certified else rec.rate_base_proposed
            )

    @api.constrains("date_start", "date_end")
    def _check_period(self):
        for rec in self:
            if rec.date_end < rec.date_start:
                raise ValidationError(
                    _("The period end comes before its start on %s.", rec.name)
                )

    @api.constrains("state", "certification_date")
    def _check_certified(self):
        for rec in self:
            if rec.state == "certified" and not rec.certification_date:
                raise ValidationError(
                    _("A certified tariff carries its certification date. "
                      "Without it, there is no way to know from when the "
                      "adjustment is due (%s).", rec.name)
                )

    @api.constrains("society", "code", "usage", "date_start", "date_end", "active")
    def _check_no_overlap(self):
        """Deux lignes qui se chevauchent feraient compter deux fois la même année."""
        for rec in self:
            if not rec.active:
                continue
            clash = self.search([
                ("id", "!=", rec.id),
                ("society", "=", rec.society),
                ("code", "=", rec.code),
                ("usage", "=", rec.usage),
                ("company_id", "=", rec.company_id.id),
                ("date_start", "<=", rec.date_end),
                ("date_end", ">=", rec.date_start),
            ], limit=1)
            if clash:
                raise ValidationError(
                    _("The period of %(new)s overlaps that of %(old)s.",
                      new=rec.name, old=clash.name)
                )

    @api.model
    def _find_for_period(self, date_start, date_end, usages, company=None):
        """Les tarifs qui touchent la période, pour les usages demandés."""
        company = company or self.env.company
        if not usages:
            return self.browse()
        return self.search([
            ("usage", "in", list(usages)),
            ("company_id", "in", [company.id, False]),
            ("date_start", "<=", date_end),
            ("date_end", ">=", date_start),
        ])

    def action_mark_certified(self):
        """Passe le tarif à homologué. Les rajustements se recalculent seuls."""
        for rec in self:
            if not rec.certification_date:
                rec.certification_date = fields.Date.today()
            rec.state = "certified"
