from odoo import _, api, fields, models


class BfBudgetLine(models.Model):
    """Le calendrier des engagements datés entre dans les deux montants qui en dépendent.

    Le socle rendait 0 pour l'engagé au-delà du comptabilisé, et None pour le
    théorique de calendrier. Ce satellite remplit les deux, sans toucher au reste.
    """

    _inherit = "bf.budget.line"

    # 🔴 `groups` AU CHAMP, pas seulement à la vue : le bassin se relève en
    # sudo (il faut les mêmes abonnements que les montants affichés), et un
    # champ caché du seul écran rendrait quand même la liste de ses ids à un
    # `read` par RPC. Les SOMMES, elles, restent libres : le chiffre est stable
    # par rôle, c'est le détail qui est fermé.
    subscription_ids = fields.Many2many(
        "subscription.subscription",
        string="Linked subscriptions",
        compute="_compute_subscription_ids",
        groups="bf_subscription.group_subscription_user",
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
        groups="bf_subscription.group_subscription_user",
        help="On-demand subscriptions linked to this budget item: they "
             "spend without a schedule, so the calendar-based theoretical "
             "amount does not cover them.",
    )
    has_subscription_without_calendar = fields.Boolean(compute="_compute_subscription_ids")

    # ── m1 : le SIGNAL d'une ligne restreinte (jamais un chiffre deviné) ──
    position_subscription_count = fields.Integer(
        string="Subscriptions on the budget item",
        compute="_compute_subscription_ids",
        help="How many subscriptions are linked to this line's budget item. "
             "On a line narrowed to analytic accounts it is the only thing "
             "the register can say: how much of each renewal belongs to the "
             "analytic account the line watches is not written anywhere.",
    )
    has_unattributed_subscriptions = fields.Boolean(
        string="Register not carried by this line",
        compute="_compute_subscription_ids",
        help="True on a line narrowed to analytic accounts whose budget item "
             "carries subscriptions: the line carries none of them, and the "
             "plain line of the same budget item carries them all.",
    )

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

    @api.depends("position_id", "source", "company_id", "analytic_account_ids")
    def _compute_subscription_ids(self):
        """Un abonnement s'accroche au POSTE ; une ligne RESTREINTE n'en porte aucun.

        🔴 Le registre des abonnements ne sait rien de l'axe analytique : rien ne
        dit quelle part d'un abonnement revient au compte qu'une ligne surveille.
        L'attribuer en entier à chaque ligne restreinte du poste compterait le
        même renouvellement plusieurs fois — et son réalisé, lui, n'arriverait
        que pondéré (la part distribuée, socle 18.0.1.5.0) : l'engagé sauterait
        au geste de comptabilisation. L'ambiguïté se déclare, elle ne se devine
        pas : la ligne restreinte ne porte RIEN du registre, la ligne nue du
        même poste porte tout.

        🔴 ET ELLE LE DÉCLARE. Un zéro muet est pire que le trop-plein qu'il
        remplace : à zéro, le bloc des engagements récurrents ET la bannière de
        l'angle mort passent `invisible`, et le lecteur d'une ligne restreinte
        ne voit plus que son poste porte un registre. `position_subscription_count`
        et `has_unattributed_subscriptions` disent donc l'existence du registre
        et le fait que CETTE ligne ne le porte pas — un signal, jamais un
        chiffre deviné. ⛔ Pas `has_subscription_without_calendar` pour ça : il
        dit autre chose (un abonnement qui dépense sans échéancier), et le
        confondre ferait crier l'angle mort du calendrier sur une ligne dont le
        calendrier n'est simplement pas le sien.
        """
        Subscription = self.env["subscription.subscription"]
        for line in self:
            on_position = line.source == "accounting" and bool(line.position_id)
            narrowed = on_position and bool(line.analytic_account_ids)
            if not on_position or narrowed:
                line.subscription_ids = Subscription
                line.subscription_no_calendar_ids = Subscription
                line.subscription_count = 0
                line.has_subscription_without_calendar = False
                # Le compte du POSTE, relevé même quand la ligne n'en porte
                # rien : c'est lui qui rend le zéro lisible.
                line.position_subscription_count = (
                    Subscription.sudo().search_count(
                        [
                            ("budget_position_id", "=", line.position_id.id),
                            ("company_id", "in", (line.company_id.id, False)),
                        ]
                    )
                    if narrowed
                    else 0
                )
                line.has_unattributed_subscriptions = bool(
                    narrowed and line.position_subscription_count
                )
                continue
            subscriptions = Subscription.sudo().search(
                [
                    ("budget_position_id", "=", line.position_id.id),
                    ("company_id", "in", (line.company_id.id, False)),
                ]
            )
            line.subscription_ids = subscriptions
            line.subscription_count = len(subscriptions)
            line.position_subscription_count = len(subscriptions)
            line.has_unattributed_subscriptions = False
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
        """Ouvre les abonnements par les droits de la personne.

        ⚠️ Le bassin se calcule en sudo (il faut les mêmes abonnements que les
        montants affichés), mais la méthode refuse d'abord à qui ne peut pas
        lire le registre : sinon elle rendrait par RPC la liste de leurs ids à
        un compte sans droit d'abonnement, et l'écran qui s'ouvrirait
        exploserait en AccessError brute au rendu. Le même garde que son jumeau
        `bf_budget_purchase.action_view_purchase_orders`.
        """
        self.ensure_one()
        self.env["subscription.subscription"].check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Budget item subscriptions"),
            "res_model": "subscription.subscription",
            "view_mode": "list,form",
            "domain": [("id", "in", self.subscription_ids.ids)],
        }
