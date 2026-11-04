from odoo import _, api, fields, models


class BfBudgetLine(models.Model):
    """Le calendrier des engagements datés entre dans les deux montants qui en dépendent.

    Le socle rendait 0 pour l'engagé au-delà du comptabilisé, et None pour le
    théorique de calendrier. Ce satellite remplit les deux, sans toucher au reste.
    """

    _inherit = "bf.budget.line"

    subscription_ids = fields.Many2many(
        "subscription.subscription",
        string="Linked subscriptions",
        compute="_compute_subscription_ids",
        help="The subscriptions whose budget item is the one on this "
             "line. The list is computed: linking a subscription to a "
             "budget item is enough, nothing needs to be copied into the "
             "budgets.",
    )
    subscription_count = fields.Integer(compute="_compute_subscription_ids")
    subscription_no_calendar_ids = fields.Many2many(
        "subscription.subscription",
        string="Subscriptions without a schedule",
        compute="_compute_subscription_ids",
        help="On-demand subscriptions linked to this budget item: they "
             "spend without a schedule, so the calendar-based theoretical "
             "amount does not cover them.",
    )
    has_subscription_without_calendar = fields.Boolean(compute="_compute_subscription_ids")

    subscription_due_to_date = fields.Monetary(
        string="Due to date",
        compute="_compute_subscription_amounts",
        currency_field="currency_id",
    )
    subscription_upcoming = fields.Monetary(
        string="Upcoming renewals",
        compute="_compute_subscription_amounts",
        currency_field="currency_id",
        help="What will fall due by the end of the fiscal year. It is "
             "known, dated and contractual: it is already committed.",
    )
    subscription_period_total = fields.Monetary(
        string="Commitments for the period",
        compute="_compute_subscription_amounts",
        currency_field="currency_id",
    )

    @api.depends("position_id", "source", "company_id")
    def _compute_subscription_ids(self):
        Subscription = self.env["subscription.subscription"]
        for line in self:
            if line.source != "accounting" or not line.position_id:
                line.subscription_ids = Subscription
                line.subscription_no_calendar_ids = Subscription
                line.subscription_count = 0
                line.has_subscription_without_calendar = False
                continue
            subscriptions = Subscription.sudo().search(
                [
                    ("budget_position_id", "=", line.position_id.id),
                    ("company_id", "in", (line.company_id.id, False)),
                ]
            )
            line.subscription_ids = subscriptions
            line.subscription_count = len(subscriptions)
            # ⚠️ Seuls les abonnements ENCORE actifs sans échéancier sont un
            # angle mort : un « à la demande » résilié ne coûtera plus rien.
            blind = subscriptions.filtered(
                lambda s: not s.budget_has_calendar and s.state in ("active", "paused")
            )
            line.subscription_no_calendar_ids = blind
            line.has_subscription_without_calendar = bool(blind)

    @api.depends(
        "subscription_ids",
        "budget_id.date_start",
        "budget_id.date_end",
        "currency_id",
    )
    def _compute_subscription_amounts(self):
        today = fields.Date.context_today(self)
        for line in self:
            due = total = 0.0
            budget = line.budget_id
            if line.subscription_ids and budget.date_start and budget.date_end:
                company = budget.company_id
                currency = line.currency_id
                for sub in line.subscription_ids:
                    if today >= budget.date_start:
                        due += sub._budget_amount_between(
                            budget.date_start, min(today, budget.date_end), currency, company
                        )
                    total += sub._budget_amount_between(
                        budget.date_start, budget.date_end, currency, company
                    )
            # L'à-venir se DÉDUIT : le total moins l'échu. Une échéance tombant
            # exactement aujourd'hui est dans l'échu, donc jamais deux fois ; et
            # le recalculer par une troisième passe d'occurrences coûtait une
            # génération de calendrier par abonnement pour un résultat jeté.
            upcoming = max(0.0, total - due)
            line.subscription_due_to_date = due
            line.subscription_upcoming = upcoming
            line.subscription_period_total = total

    def _get_extra_commitments(self):
        """Les renouvellements à venir sont déjà engagés.

        ⚠️ On n'ajoute QUE l'à-venir. L'échu est déjà dans le réalisé dès que la
        facture est comptabilisée ; l'ajouter aussi doublerait la dépense la plus
        prévisible du budget, celle qu'on croit justement la mieux tenue.
        """
        base = super()._get_extra_commitments()
        return base + self.subscription_upcoming

    def _calendar_commitment_components(self):
        """Le calendrier des renouvellements, déclaré au socle qui combine.

        Depuis bf_budget 18.0.1.4.0, le théorique de calendrier est combiné
        par le socle : chaque satellite DÉCLARE son couple (dû à ce jour,
        total de la période) au lieu de le calculer lui-même, pour que le
        reliquat au prorata se mesure contre le plan non couvert par
        l'ensemble des calendriers — jamais deux fois.
        """
        components = super()._calendar_commitment_components()
        # ⚠️ Un calendrier qui ne CONTRIBUE rien dans la fenêtre (un ponctuel
        # d'un exercice passé, résilié mais encore lié au poste) ne doit pas
        # basculer la base en « calendar » : il déclarait (0, 0) et le poste
        # entier passait au reliquat, alors qu'aucune échéance ne le date.
        if self.subscription_period_total:
            components.append(
                (self.subscription_due_to_date, self.subscription_period_total)
            )
        return components

    def action_view_subscriptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Budget item subscriptions"),
            "res_model": "subscription.subscription",
            "view_mode": "list,form",
            "domain": [("id", "in", self.subscription_ids.ids)],
        }
