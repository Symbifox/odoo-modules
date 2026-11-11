from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import BfBudgetAnalyticCommon


@tagged("post_install", "-at_install")
class TestUnfold(BfBudgetAnalyticCommon):
    def test_the_unfold_is_the_measure(self):
        """Le domaine de l'action EST celui de la mesure : un seul énoncé."""
        budget, line = self._labour_line()
        action = line.action_view_analytic_lines()
        self.assertEqual(action["res_model"], "account.analytic.line")
        self.assertEqual(action["domain"], line._actual_source()[2])
        self.assertFalse(action["context"]["create"])

    def test_a_ledger_line_refuses_to_unfold(self):
        budget = self._make_budget()
        with self.assertRaises(UserError):
            budget.line_ids.action_view_analytic_lines()

    def test_an_incomplete_line_has_nothing_to_unfold(self):
        """La sentinelle du socle (« ne matche rien ») refuse proprement.

        Une ligne de coût interne sans compte analytique (greffon qui desserre
        la contrainte, formulaire en saisie) n'ouvre pas la table analytique
        entière : elle n'a rien derrière elle.
        """
        budget, _line = self._labour_line()
        ghost = self.env["bf.budget.line"].new(
            {"budget_id": budget.id, "source": "internal_cost"}
        )
        with self.assertRaises(UserError):
            ghost.action_view_analytic_lines()

    def test_unfolding_labour_shows_only_your_own_timesheets(self):
        """🔴 §11 : déplier un budget de main-d'œuvre, c'est lire des salaires.

        Anne et Bob ont chacun leurs heures. Anne lit la SOMME de la ligne
        (260 $, les salaires des deux, agrégés) ; mais le dépliage, rendu sous
        SES droits, ne lui montre que ses propres feuilles de temps. Jamais
        uid 1 ici, et le cache est invalidé après les lectures du décor.
        """
        budget, line = self._labour_line()
        self._timesheet(self.employee_anne, 2.0)
        row_bob = self._timesheet(self.employee_bob, 3.0)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 260.0)

        self.env.invalidate_all()
        as_anne = line.with_user(self.user_anne)
        # La somme est STABLE par rôle : Anne lit le même réalisé que l'admin.
        self.assertEqual(as_anne.amount_actual, 260.0)
        action = as_anne.action_view_analytic_lines()
        visible = (
            self.env["account.analytic.line"]
            .with_user(self.user_anne)
            .search(action["domain"])
        )
        self.assertEqual(visible.employee_id, self.employee_anne)
        self.assertNotIn(row_bob.id, visible.ids)
        # La ligne de Bob n'est pas lisible non plus en la visant directement.
        hidden = (
            self.env["account.analytic.line"]
            .with_user(self.user_anne)
            .search([("id", "=", row_bob.id)])
        )
        self.assertFalse(hidden)

    def test_a_reader_without_analytic_rights_is_refused_cleanly(self):
        """L'ACL d'`account.analytic.line` vient des groupes feuilles de temps,
        compta analytique ou achats : un lecteur de budget nu ne l'a pas. Il
        lit les sommes — jamais le détail, pas même son existence."""
        budget, line = self._labour_line()
        self._timesheet(self.employee_anne, 2.0)
        line.invalidate_recordset()
        self.env.invalidate_all()
        as_bare = line.with_user(self.user_bare)
        self.assertEqual(as_bare.amount_actual, 80.0)
        self.assertEqual(as_bare.timesheet_hours, 2.0)
        with self.assertRaises(AccessError):
            as_bare.action_view_analytic_lines()

    def test_unfolding_a_narrowed_accounting_line_respects_rights_too(self):
        """Les parts comptables se déplient sous les droits comptables.

        Le domaine des parts traverse l'écriture (`move_line_id.…`) : son
        évaluation exige la lecture du grand livre. Anne (feuilles de temps
        seulement) est refusée PROPREMENT par l'action — pas par une
        AccessError brute au rendu. Un œil comptable (compta analytique +
        lecture du grand livre) voit les parts, et seulement elles : sa règle
        ne couvre pas les lignes de projet.
        """
        budget = self.env["bf.budget"].create(
            {
                "name": "Parts et droits",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        line = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 600.0,
            }
        )
        self._post_bill(
            self.account_software,
            1000.0,
            distribution={self.analytic_a: 50.0, self.analytic_b: 50.0},
        )
        accountant = self.env["res.users"].with_context(no_reset_password=True).create(
            {
                "name": "Œil comptable",
                "login": "comptable.budget@essai.invalid",
                "company_id": self.company.id,
                "company_ids": [Command.set([self.company.id])],
                "groups_id": [
                    Command.set(
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("bf_budget.group_bf_budget_user").id,
                            self.env.ref("analytic.group_analytic_accounting").id,
                            self.env.ref("account.group_account_readonly").id,
                        ]
                    )
                ],
            }
        )
        line.invalidate_recordset()
        self.env.invalidate_all()

        with self.assertRaises(AccessError):
            line.with_user(self.user_anne).action_view_analytic_lines()

        self.env.invalidate_all()
        action = line.with_user(accountant).action_view_analytic_lines()
        seen_by_accountant = (
            self.env["account.analytic.line"]
            .with_user(accountant)
            .search(action["domain"])
        )
        self.assertEqual(len(seen_by_accountant), 1)
        self.assertEqual(seen_by_accountant.amount, -500.0)
