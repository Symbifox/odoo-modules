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
    _description = "Tarif de redevance musicale"
    _inherit = ["mail.thread"]
    _order = "society, code, date_start desc"

    name = fields.Char(compute="_compute_name", store=True)
    society = fields.Selection(
        selection=[("socan", "SOCAN"), ("resound", "Ré:Sonne")],
        string="Société de gestion",
        required=True,
        tracking=True,
    )
    code = fields.Char(
        string="Tarif",
        required=True,
        tracking=True,
        help="Le numéro tel que la Commission le désigne : 15.A, 15.B, 3.B.",
    )
    label = fields.Char(string="Intitulé", required=True)
    usage = fields.Selection(
        selection=[
            ("background", "Musique d'ambiance"),
            ("on_hold", "Attente téléphonique"),
        ],
        string="Usage visé",
        required=True,
        default="background",
    )
    date_start = fields.Date(string="Début de la période", required=True, tracking=True)
    date_end = fields.Date(string="Fin de la période", required=True, tracking=True)
    basis = fields.Selection(
        selection=[
            ("area_sqm", "Par mètre carré"),
            ("area_sqft", "Par pied carré"),
            ("area_sqm_day", "Par mètre carré et par jour d'exploitation"),
            ("area_sqft_day", "Par pied carré et par jour d'exploitation"),
            ("line", "Par ligne téléphonique"),
            ("flat", "Montant fixe"),
        ],
        string="Assiette",
        required=True,
        default="area_sqm",
    )
    state = fields.Selection(
        selection=[("proposed", "Proposé"), ("certified", "Homologué")],
        string="État",
        required=True,
        default="proposed",
        tracking=True,
        help="Proposé : publié par la Commission mais pas encore homologué. "
             "Ce que le client paie sous un tarif proposé reste révisable.",
    )
    rate_proposed = fields.Float(
        string="Taux proposé", digits=(16, 5), tracking=True,
        help="Taux par unité d'assiette. Sur une assiette par ligne "
             "téléphonique, il ne vise que les lignes SUPPLÉMENTAIRES : "
             "la première est facturée au montant de base.",
    )
    rate_base_proposed = fields.Monetary(
        string="Montant de base proposé", currency_field="currency_id",
        help="Montant dû pour la première unité, quand le tarif est rédigé "
             "« tant pour la première, tant pour chaque unité de plus ».",
    )
    minimum_proposed = fields.Monetary(
        string="Minimum proposé", currency_field="currency_id",
        help="Redevance annuelle minimale, quand le tarif en prévoit une.",
    )
    rate_certified = fields.Float(
        string="Taux homologué", digits=(16, 5), tracking=True,
    )
    rate_base_certified = fields.Monetary(
        string="Montant de base homologué", currency_field="currency_id",
    )
    minimum_certified = fields.Monetary(
        string="Minimum homologué", currency_field="currency_id",
    )
    certification_date = fields.Date(string="Date d'homologation", tracking=True)
    publication_date = fields.Date(string="Date de publication du projet")
    source_url = fields.Char(string="Source")
    rate_confirmed = fields.Boolean(
        string="Taux relevé à la source",
        default=False,
        help="Faux tant que le taux n'a pas été lu sur le texte du tarif lui-même. "
             "Un taux non relevé ne sert pas de promesse commerciale.",
    )
    seasonal_half = fields.Boolean(
        string="Demi-tarif saisonnier",
        help="Le tarif prévoit la moitié du taux pour un établissement "
             "exploité moins de six mois par année.",
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
        string="Taux en vigueur", compute="_compute_in_force", digits=(16, 5),
    )
    rate_base_in_force = fields.Monetary(
        string="Montant de base en vigueur", compute="_compute_in_force",
        currency_field="currency_id",
    )
    minimum_in_force = fields.Monetary(
        string="Minimum en vigueur", compute="_compute_in_force",
        currency_field="currency_id",
    )

    _sql_constraints = [
        (
            "period_unique",
            "unique(society, code, usage, date_start, company_id)",
            "Une même période ne peut porter qu'une ligne par tarif : "
            "le taux proposé et le taux homologué vivent sur la même ligne.",
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
                    _("La fin de la période précède son début sur %s.", rec.name)
                )

    @api.constrains("state", "certification_date")
    def _check_certified(self):
        for rec in self:
            if rec.state == "certified" and not rec.certification_date:
                raise ValidationError(
                    _("Un tarif homologué porte sa date d'homologation. "
                      "Sans elle, on ne sait pas à partir de quand le "
                      "rajustement est dû (%s).", rec.name)
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
                    _("La période de %(new)s chevauche celle de %(old)s.",
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
