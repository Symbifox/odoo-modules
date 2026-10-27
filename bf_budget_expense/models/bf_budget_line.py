from odoo import _, api, fields, models


def names_analytic(distribution, analytic_ids):
    """Une distribution analytique nomme-t-elle l'un de ces comptes ?

    ⚠️ Une clé peut réunir plusieurs comptes (« 3,7 ») quand la société a
    plusieurs plans. Comparer la clé entière à un identifiant manquerait ces
    lignes-là.
    """
    wanted = {str(analytic_id) for analytic_id in analytic_ids}
    for key in (distribution or {}):
        if wanted.intersection(str(key).split(",")):
            return True
    return False


class BfBudgetLine(models.Model):
    """Les notes de frais approuvées entrent dans l'engagé du poste.

    Le socle rend zéro pour l'engagé au-delà du comptabilisé ; ce satellite y
    ajoute les notes approuvées dont l'écriture n'est pas encore comptabilisée.
    Dès qu'elle l'est, la note passe dans le réalisé et sort de l'engagé
    supplémentaire : le total engagé ne bouge pas, il change seulement de case.

    🔴 LE GARDE EST LE LIEN, JAMAIS L'ÉTAT. `hr.expense.state` est calculé et
    vaut « approved » aussi bien pour une note dont l'écriture est au brouillon
    que pour une note dont l'écriture est comptabilisée mais pas encore payée.
    Dans le second cas, la dépense est déjà dans le réalisé. On lit donc l'état
    de l'ÉCRITURE : brouillon, elle est engagée ; comptabilisée, elle est
    réalisée ; jamais les deux.

    🔴 ET LE LIEN QUI TIENT EST CELUI DE LA LIGNE, PAS CELUI DE LA PIÈCE.
    `account.move.expense_sheet_id` est `copy=False`, et Odoo l'EFFACE sur la
    facture dès qu'on lui fait un avoir (`_reverse_moves`) ou qu'on l'annule.
    La pièce redevient alors « approuvée sans écriture » alors que sa dépense
    est dans le réalisé. `account.move.line.expense_id`, lui, est `copy=True` :
    il suit la facture, l'avoir et le brouillon recréé par une extourne. C'est
    lui qui dit si une note est déjà portée en comptabilité.

    Deux sources, disjointes par construction :

    * une note approuvée a, dans Odoo 18, une écriture créée au brouillon dès
      l'approbation. On lit les lignes de cette écriture : compte, date et
      montant sont ceux qu'elle aura une fois comptabilisée, y compris si un
      comptable les a retouchés entre-temps ;
    * une note qu'aucune écriture vivante ne porte (pièce soumise, ou approuvée
      dont l'écriture a été supprimée) n'a rien à lire : le montant est alors
      calculé par le moteur de taxes d'Odoo, comme le ferait la
      comptabilisation.
    """

    _inherit = "bf.budget.line"

    expense_committed = fields.Monetary(
        string="Approved expenses",
        compute="_compute_expense_amounts",
        currency_field="currency_id",
        help="Approved expense reports whose journal entry is not posted "
             "yet. The money will go out: it is already committed.",
    )
    expense_committed_count = fields.Integer(
        string="Approved expenses (count)", compute="_compute_expense_amounts"
    )
    expense_pending = fields.Monetary(
        string="Submitted expenses",
        compute="_compute_expense_amounts",
        currency_field="currency_id",
        help="Submitted but not yet approved. Shown for information only: "
             "an expense nobody has approved is not a decision, so it is "
             "in no amount of the budget.",
    )
    expense_pending_count = fields.Integer(
        string="Submitted expenses (count)", compute="_compute_expense_amounts"
    )

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------
    # ⚠️ Tout est lu en sudo, et c'est voulu : un gestionnaire de budget n'a pas
    # à être approbateur de notes de frais ni comptable pour voir ce qui est
    # engagé sur son poste. Sans sudo, la règle d'Odoo ne lui laisserait voir
    # que SES notes, et le montant serait faux sans que rien ne le signale. La
    # ligne n'expose que des sommes et des nombres, jamais une note ni un
    # employé.

    def _bf_expense_applies(self):
        """Une note de frais est une charge lue en comptabilité.

        Une ligne de coût interne lit les feuilles de temps, et un budget de
        revenus lit des produits : ni l'un ni l'autre n'a d'engagement à tirer
        d'une note de frais.
        """
        self.ensure_one()
        budget = self.budget_id
        return (
            self.source == "accounting"
            and self.budget_type == "expense"
            and bool(self.position_id)
            and bool(budget.date_start and budget.date_end)
        )

    def _bf_expense_draft_lines(self):
        """Les lignes AU BROUILLON des écritures de notes approuvées, sur ce poste.

        L'écriture est retrouvée par ses lignes de note (`expense_id`) aussi bien
        que par sa pièce : un brouillon recréé par une extourne n'est plus lié à
        la pièce, mais ses lignes le sont toujours à la note. On prend alors
        TOUTES les lignes de l'écriture sur les comptes du poste, y compris une
        taxe non récupérable, qui n'a pas d'`expense_id` à elle.
        """
        self.ensure_one()
        budget = self.budget_id
        lines = self.env["account.move.line"].sudo().search(
            [
                ("parent_state", "=", "draft"),
                "|",
                ("move_id.expense_sheet_id.approval_state", "=", "approve"),
                ("move_id.line_ids.expense_id.sheet_id.approval_state", "=", "approve"),
                ("account_id", "in", self.position_id.account_ids.ids),
                ("date", ">=", budget.date_start),
                ("date", "<=", budget.date_end),
                ("company_id", "=", budget.company_id.id),
            ]
        )
        lines = lines.filtered(lambda aml: aml.move_id in self._bf_expense_live_drafts(lines.move_id))
        if self.analytic_account_ids:
            analytic_ids = self.analytic_account_ids.ids
            lines = lines.filtered(
                lambda aml: names_analytic(aml.analytic_distribution, analytic_ids)
            )
        return lines

    @api.model
    def _bf_expense_live_drafts(self, moves):
        """Écarter les brouillons qui ne sont qu'une COPIE d'une écriture de note.

        Un brouillon lié à sa pièce est l'écriture de la note : il compte. Un
        brouillon trouvé seulement par ses lignes (`expense_id`, copy=True) est
        soit un avoir, soit le brouillon recréé par une extourne, soit une simple
        copie faite par « Dupliquer ». Les deux premiers comptent. La copie, non :
        sa note est déjà portée ailleurs, comptabilisée (solde net non nul) ou au
        brouillon lié à la pièce. La compter doublerait l'engagé tant qu'elle vit.
        """
        linked = moves.filtered("expense_sheet_id")
        orphans = moves - linked
        kept = linked | orphans.filtered("reversed_entry_id")
        candidates = orphans - kept
        if not candidates:
            return kept
        expenses = candidates.line_ids.expense_id
        AML = self.env["account.move.line"].sudo()
        groups = AML._read_group(
            [
                ("expense_id", "in", expenses.ids),
                ("parent_state", "=", "posted"),
                ("display_type", "=", "product"),
            ],
            groupby=["expense_id"],
            aggregates=["balance:sum"],
        )
        carried = {
            expense.id for expense, total in groups
            if not expense.company_currency_id.is_zero(total)
        }
        carried |= set(
            AML.search(
                [
                    ("expense_id", "in", expenses.ids),
                    ("parent_state", "=", "draft"),
                    ("move_id.expense_sheet_id", "!=", False),
                ]
            ).expense_id.ids
        )
        for move in candidates:
            if not carried.intersection(move.line_ids.expense_id.ids):
                kept |= move
        return kept

    @api.model
    def _bf_expense_unbooked(self, company, approval_state):
        """Les notes qu'aucune écriture vivante ne porte, avec compte, date et montant.

        Une note est « portée » quand ses lignes de note, dans les écritures non
        annulées, laissent un solde net. Le solde net, et non la simple présence
        d'une ligne : une note remise à zéro puis soumise de nouveau garde sa
        facture ET l'extourne de celle-ci, qui s'annulent. Elle n'est plus portée,
        et doit redevenir visible.

        Rend une liste de tuples (note, compte, date, montant), calculée une seule
        fois pour tout un lot de lignes.
        """
        Expense = self.env["hr.expense"].sudo()
        expenses = Expense.search(
            [
                ("company_id", "=", company.id),
                ("sheet_id.approval_state", "=", approval_state),
            ]
        )
        if not expenses:
            return []
        groups = self.env["account.move.line"].sudo()._read_group(
            [
                ("expense_id", "in", expenses.ids),
                ("parent_state", "!=", "cancel"),
                ("display_type", "=", "product"),
            ],
            groupby=["expense_id"],
            aggregates=["balance:sum"],
        )
        booked = Expense.browse(
            [
                expense.id
                for expense, total in groups
                if not expense.company_currency_id.is_zero(total)
            ]
        )
        return [
            (
                expense,
                expense._get_base_account(),
                expense._bf_budget_date(),
                expense._bf_budget_amount(),
            )
            for expense in expenses - booked
        ]

    def _bf_expense_on_line(self, unbooked):
        """Ne garder, parmi les notes non portées, que celles qui tomberont ici."""
        self.ensure_one()
        budget = self.budget_id
        accounts = self.position_id.account_ids
        analytic_ids = self.analytic_account_ids.ids
        kept = []
        for expense, account, date, amount in unbooked:
            if account not in accounts:
                continue
            if not date or date < budget.date_start or date > budget.date_end:
                continue
            if analytic_ids and not names_analytic(expense.analytic_distribution, analytic_ids):
                continue
            kept.append((expense, amount))
        return kept

    @api.depends(
        "source",
        "budget_type",
        "position_id.account_ids",
        "analytic_account_ids",
        "budget_id.date_start",
        "budget_id.date_end",
        "budget_id.company_id",
    )
    def _compute_expense_amounts(self):
        # Une lecture des notes par société et par état pour tout le lot, pas une
        # par ligne : chaque note y passe par le moteur de taxes.
        pools = {}

        def pool(company, approval_state):
            key = (company.id, approval_state)
            if key not in pools:
                pools[key] = self._bf_expense_unbooked(company, approval_state)
            return pools[key]

        Expense = self.env["hr.expense"].sudo()
        for line in self:
            committed = pending = 0.0
            committed_count = pending_count = 0
            if line._bf_expense_applies():
                company = line.budget_id.company_id
                draft_lines = line._bf_expense_draft_lines()
                approved = line._bf_expense_on_line(pool(company, "approve"))
                submitted = line._bf_expense_on_line(pool(company, "submit"))
                committed = sum(draft_lines.mapped("balance")) + sum(
                    amount for _expense, amount in approved
                )
                counted = draft_lines.expense_id | Expense.browse(
                    [expense.id for expense, _amount in approved]
                )
                committed_count = len(counted)
                pending = sum(amount for _expense, amount in submitted)
                pending_count = len(submitted)
            line.expense_committed = committed
            line.expense_committed_count = committed_count
            line.expense_pending = pending
            line.expense_pending_count = pending_count

    def _get_extra_commitments(self):
        """Les notes approuvées non comptabilisées sont déjà engagées.

        ⚠️ Seulement l'approuvé : le soumis n'est pas une décision.
        """
        base = super()._get_extra_commitments()
        return base + self.expense_committed

    def action_view_expenses(self):
        """Ouvre les notes, SANS sudo : chacun n'y voit que ce que ses droits permettent."""
        self.ensure_one()
        company = self.budget_id.company_id
        ids = set(self._bf_expense_draft_lines().expense_id.ids)
        for state in ("approve", "submit"):
            for expense, _amount in self._bf_expense_on_line(
                self._bf_expense_unbooked(company, state)
            ):
                ids.add(expense.id)
        return {
            "type": "ir.actions.act_window",
            "name": _("Expenses not yet posted"),
            "res_model": "hr.expense",
            "view_mode": "list,form",
            "domain": [("id", "in", sorted(ids))],
        }
