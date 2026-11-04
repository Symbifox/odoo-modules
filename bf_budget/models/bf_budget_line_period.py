from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


# Ce qu'une période ne laisse plus toucher hors brouillon. Les DATES en font
# partie : elles portent le théorique, donc l'alerte — déplacer une période
# réécrivait le plan par le champ d'à côté. Reparenter (`line_id`) aussi : une
# période qui change de ligne change deux ventilations d'un coup.
PERIOD_PLAN_FIELDS = {"amount_planned", "date_start", "date_end", "line_id"}


class BfBudgetLinePeriod(models.Model):
    """La répartition mensuelle d'une ligne budgétaire.

    Elle existe pour une seule raison : une dépense annuelle ne se lisse pas sur
    douze mois. Un renouvellement de domaines qui tombe en mars doit être prévu en
    mars, sinon le théorique déclare un dépassement en mars et un sous-emploi les
    onze autres mois, et l'alerte devient du bruit.
    """

    _name = "bf.budget.line.period"
    _description = "Monthly breakdown of a budget line"
    _order = "line_id, sequence, date_start"

    line_id = fields.Many2one(
        "bf.budget.line", required=True, ondelete="cascade", index=True
    )
    budget_id = fields.Many2one(related="line_id.budget_id", store=True, index=True)
    currency_id = fields.Many2one(related="line_id.currency_id")
    sequence = fields.Integer(default=0)
    date_start = fields.Date(required=True)
    date_end = fields.Date(required=True)
    amount_planned = fields.Monetary(string="Planned", currency_field="currency_id")
    name = fields.Char(compute="_compute_name", store=True)

    @api.model_create_multi
    def create(self, vals_list):
        """🔴 Une période ne NAÎT pas sous un budget sorti du brouillon.

        Sans cette garde, un `create` par RPC ajoutait un mois à la ventilation
        d'un budget ouvert — et donc au théorique — sans passer par aucun des
        `write` gardés. Les chemins internes (génération des périodes d'une
        ligne neuve, régénération, redatage) ne travaillent que sur un
        brouillon : ils passent.

        🔴 La garde se joue APRÈS `super().create()`, sur les enregistrements,
        jamais sur `vals_list` : l'ORM n'injecte les défauts du contexte
        (`default_line_id`) que dans `super()`, et une première version qui
        lisait `vals` ne voyait RIEN passer par
        `with_context(default_line_id=…).create({...})` — la ventilation d'un
        budget ouvert se réécrivait sans refus.
        Lever ici annule l'insertion avec le reste de la transaction.
        """
        periods = super().create(vals_list)
        locked = periods.filtered(lambda p: p.budget_id.state != "draft")
        if locked:
            raise UserError(
                _(
                    "The monthly breakdown of \"%(name)s\" can no longer "
                    "be edited: its budget is no longer a draft.",
                    name=locked[0].line_id.display_name,
                )
            )
        return periods

    def write(self, vals):
        """🔴 Le gel du plan descend jusqu'à la période, DATES comprises.

        La vue met déjà la ventilation en lecture seule hors brouillon, mais un
        `write` par RPC passait à côté : la courbe mensuelle d'un budget ouvert
        se réécrivait sans trace, et c'est elle qui porte le théorique, donc les
        alertes. Un budget qu'on retouche en douce ne mesure plus rien. Et le
        montant n'est pas la seule porte : déplacer les dates d'une période, ou
        la reparenter sur une autre ligne, réécrit le même théorique.
        """
        touched = PERIOD_PLAN_FIELDS & set(vals)
        if touched:
            locked = self.filtered(lambda p: p.budget_id.state != "draft")
            if locked:
                raise UserError(
                    _(
                        "The monthly breakdown of \"%(name)s\" can no longer "
                        "be edited: its budget is no longer a draft.",
                        name=locked[0].line_id.display_name,
                    )
                )
        if vals.get("line_id"):
            dest = self.env["bf.budget.line"].browse(vals["line_id"])
            if dest.state != "draft":
                raise UserError(
                    _(
                        "The monthly breakdown of \"%(name)s\" can no longer "
                        "be edited: its budget is no longer a draft.",
                        name=dest.display_name,
                    )
                )
        return super().write(vals)

    def unlink(self):
        """Une période d'un budget sorti du brouillon ne se supprime pas.

        ⚠️ La suppression d'une LIGNE de brouillon passe par `ondelete=cascade`,
        qui ne rappelle pas `unlink` : cette garde ne bloque que le geste
        direct, et c'est le seul qui existe hors brouillon.
        """
        locked = self.filtered(lambda p: p.budget_id.state != "draft")
        if locked:
            raise UserError(
                _(
                    "The monthly breakdown of \"%(name)s\" can no longer "
                    "be edited: its budget is no longer a draft.",
                    name=locked[0].line_id.display_name,
                )
            )
        return super().unlink()

    @api.depends("date_start")
    def _compute_name(self):
        for period in self:
            period.name = period.date_start.strftime("%Y-%m") if period.date_start else ""

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for period in self:
            if period.date_end < period.date_start:
                raise ValidationError(
                    _("A period cannot end before it starts.")
                )
