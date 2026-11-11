from odoo import Command, fields
from odoo.tests import tagged

from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestBfBudgetAmounts(BfBudgetCommon):
    def test_actual_reads_only_posted_entries(self):
        budget = self._make_budget()
        line = budget.line_ids
        self._post_bill(self.account_software, 300.0)
        self._post_bill(self.account_software, 500.0, post=False)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 300.0)

    def test_expense_actual_is_positive(self):
        """Une charge est un débit : on la rend « dépensé », positif."""
        budget = self._make_budget()
        self._post_bill(self.account_software, 250.0)
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_actual, 250.0)

    def test_revenue_actual_is_positive_too(self):
        """Un produit est un crédit : son solde est négatif, on le retourne."""
        budget = self._make_budget(budget_type="revenue", positions=[self.position_revenue])
        self._post_revenue(900.0)
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_actual, 900.0)

    def test_other_accounts_are_ignored(self):
        budget = self._make_budget()
        self._post_bill(self.account_telecom, 400.0)
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_actual, 0.0)

    def test_entries_outside_the_period_are_ignored(self):
        budget = self._make_budget()
        self._post_bill(
            self.account_software, 700.0, date=self.date_start.replace(year=self.date_start.year - 1)
        )
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_actual, 0.0)

    def test_analytic_narrowing_filters_the_accounting_read(self):
        budget = self._make_budget()
        line = budget.line_ids
        self._post_bill(self.account_software, 100.0, analytic=self.analytic_a)
        self._post_bill(self.account_software, 250.0, analytic=self.analytic_b)
        self._post_bill(self.account_software, 40.0)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 390.0)
        line.write({"analytic_account_ids": [Command.set(self.analytic_a.ids)]})
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 100.0)

    def test_a_partial_distribution_counts_its_share_once(self):
        """🔴 Deux lignes qui se partagent une écriture ne la comptent qu'une fois.

        Une facture ventilée A:50/B:50 comptait 100 % sur la ligne qui surveille
        A ET 100 % sur celle qui surveille B : la mesure restreignait les
        écritures puis sommait leur plein solde. La mesure pondérée lit la PART
        (les lignes analytiques adossées), et le total du budget redevient le
        total de la facture.
        """
        budget = self.env["bf.budget"].create(
            {
                "name": "Parts",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        line_a = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 600.0,
            }
        )
        line_b = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [Command.set(self.analytic_b.ids)],
                "amount_planned": 600.0,
            }
        )
        both = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [
                    Command.set((self.analytic_a | self.analytic_b).ids)
                ],
                "amount_planned": 1200.0,
            }
        )
        self._post_bill(
            self.account_software,
            1000.0,
            distribution={self.analytic_a: 50.0, self.analytic_b: 50.0},
        )
        budget.invalidate_recordset()
        self.assertEqual(line_a.amount_actual, 500.0)
        self.assertEqual(line_b.amount_actual, 500.0)
        # Une ligne qui surveille les deux comptes lit les deux parts : 1 000,
        # jamais 2 000 (chaque part est UNE ligne analytique, comptée une fois).
        self.assertEqual(both.amount_actual, 1000.0)

    def _second_plan_account(self, name="Axe C"):
        """Un compte d'un SECOND plan racine : sa colonne n'est pas celle du
        plan du décor, et c'est tout l'enjeu du OR."""
        plan = self.env["account.analytic.plan"].create({"name": "Second axe budget"})
        account = self.env["account.analytic.account"].create(
            {"name": name, "plan_id": plan.id}
        )
        self.assertNotEqual(
            account.root_plan_id._column_name(),
            self.analytic_a.root_plan_id._column_name(),
        )
        return account

    def test_a_line_naming_two_root_plans_reads_both_axes(self):
        """🔴 La branche OR de `_analytic_column_domain`, sans laquelle la ligne
        lit ZÉRO pour toujours.

        `account.analytic.line` porte une colonne par plan racine, et une part
        ne remplit que la colonne de SON plan. Deux conditions de colonnes
        collées sans le « | » forment un ET : aucune ligne analytique ne peut
        satisfaire les deux, la ligne budgétaire lit 0,00 $ et le poste a l'air
        d'un poste où rien n'a été dépensé — en silence, pour toujours.
        """
        analytic_c = self._second_plan_account()
        budget = self._make_budget()
        line = budget.line_ids
        line.write(
            {"analytic_account_ids": [Command.set((self.analytic_a | analytic_c).ids)]}
        )
        self._post_bill(self.account_software, 100.0, analytic=self.analytic_a)
        self._post_bill(self.account_software, 250.0, analytic=analytic_c)
        # Un compte du MÊME plan que A, non surveillé : le OR ne doit pas
        # devenir un « tout ce qui est analytique ».
        self._post_bill(self.account_software, 40.0, analytic=self.analytic_b)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 350.0)

    def test_the_actual_of_a_composite_key_counts_its_analytic_line_once(self):
        """Clé composée « A,C » : UNE ligne analytique aux DEUX colonnes.

        C'est ce que le cœur 18 écrit quand une distribution couvre plusieurs
        plans (`analytic.mixin._merge_distribution` fusionne, puis
        `account.move.line._prepare_analytic_distribution_line` pose une ligne
        par clé, en remplissant la colonne de chaque compte de la clé). Une
        ligne budgétaire qui nomme les deux axes la compte donc UNE fois, pas
        deux : le OR porte sur les colonnes, et la somme porte sur les lignes.
        Seul l'ENGAGÉ d'une clé composée était éprouvé (chez les satellites) ;
        le réalisé ne l'était nulle part.
        """
        analytic_c = self._second_plan_account()
        budget = self._make_budget()
        line = budget.line_ids
        line.write(
            {"analytic_account_ids": [Command.set((self.analytic_a | analytic_c).ids)]}
        )
        move = self._post_bill(self.account_software, 1000.0, post=False)
        charge = move.line_ids.filtered(
            lambda aml: aml.account_id == self.account_software
        )
        charge.analytic_distribution = {
            "%s,%s" % (self.analytic_a.id, analytic_c.id): 100.0
        }
        move.action_post()
        parts = self.env["account.analytic.line"].search(
            [("move_line_id", "=", charge.id)]
        )
        self.assertEqual(len(parts), 1)
        column_a = self.analytic_a.root_plan_id._column_name()
        column_c = analytic_c.root_plan_id._column_name()
        self.assertEqual(parts[column_a], self.analytic_a)
        self.assertEqual(parts[column_c], analytic_c)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 1000.0)
        # Et une ligne qui n'en nomme qu'un seul lit la même part entière :
        # la clé porte UN pourcentage, pas un par compte.
        only_c = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [Command.set(analytic_c.ids)],
                "amount_planned": 1000.0,
            }
        )
        only_c.invalidate_recordset()
        self.assertEqual(only_c.amount_actual, 1000.0)

    def test_a_filtered_line_is_windowed_on_the_journal_items_date(self):
        """La fenêtre d'une ligne filtrée est la date de l'ÉCRITURE.

        Le grand livre est la vérité ; la ligne analytique n'est que la
        pondération. Redater la ligne analytique à la main ne fait pas entrer
        une écriture d'un autre exercice dans le budget.
        """
        budget = self._make_budget()
        line = budget.line_ids
        line.write({"analytic_account_ids": [Command.set(self.analytic_a.ids)]})
        move = self._post_bill(
            self.account_software,
            700.0,
            date=self.date_start.replace(year=self.date_start.year - 1),
            analytic=self.analytic_a,
        )
        column = self.analytic_a.root_plan_id._column_name()
        carried = self.env["account.analytic.line"].search(
            [(column, "=", self.analytic_a.id), ("move_line_id", "!=", False)]
        )
        self.assertTrue(carried)
        carried.sudo().write({"date": fields.Date.context_today(self.env.user)})
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 0.0)
        self.assertTrue(move.line_ids)

        # Et l'autre sens : une écriture DANS la fenêtre (à sa borne même, le
        # premier jour) compte, même si sa ligne analytique est redatée dehors.
        self._post_bill(
            self.account_software, 120.0, date=self.date_start, analytic=self.analytic_a
        )
        inside = self.env["account.analytic.line"].search(
            [
                (column, "=", self.analytic_a.id),
                ("move_line_id", "!=", False),
                ("date", "=", self.date_start),
            ]
        )
        self.assertTrue(inside)
        inside.sudo().write(
            {"date": self.date_start.replace(year=self.date_start.year - 1)}
        )
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 120.0)

    def test_an_incomplete_line_reads_nothing_not_everything(self):
        """La sentinelle d'une ligne incomplète ne matche RIEN, jamais tout.

        Un domaine vide (`[]`) matche TOUTE la table : un greffon qui desserre
        la contrainte (coût interne sans compte analytique existe en clientèle)
        et fait `search(line._actual_source()[2])` balaierait la comptabilité
        entière. Les lignes fantômes se construisent par `new()` : la
        contrainte ne s'évalue qu'au create/write, c'est le seul chemin vers
        une ligne incomplète — le même que celui d'un formulaire en saisie.
        """
        budget = self._make_budget()
        ghost = self.env["bf.budget.line"].new(
            {"budget_id": budget.id, "source": "accounting"}
        )
        self.assertEqual(ghost._actual_source()[2], [("id", "=", False)])
        self.assertEqual(ghost._read_actual(), 0.0)
        ghost_internal = self.env["bf.budget.line"].new(
            {"budget_id": budget.id, "source": "internal_cost"}
        )
        self.assertEqual(ghost_internal._actual_source()[2], [("id", "=", False)])
        self.assertEqual(ghost_internal._read_actual(), 0.0)

    def test_a_posted_refund_can_pull_a_filtered_line_negative(self):
        """Un avoir plus gros que les factures rend un réalisé NÉGATIF.

        Le signe n'est pas une valeur absolue : la part analytique d'un avoir
        est positive sur une charge, et la ligne doit le montrer tel quel.
        """
        budget = self._make_budget()
        line = budget.line_ids
        line.write({"analytic_account_ids": [Command.set(self.analytic_a.ids)]})
        self._post_bill(self.account_software, 100.0, analytic=self.analytic_a)
        self._post_bill(self.account_software, -300.0, analytic=self.analytic_a)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, -200.0)

    def test_another_companys_entries_stay_out_of_a_filtered_line(self):
        """⚠️ Depuis Odoo 18 un compte PEUT être partagé entre sociétés.

        Une écriture d'une autre société sur le MÊME compte partagé, ventilée
        sur le même compte analytique, ne doit pas entrer : le filtre de
        société de la mesure est porteur, pas décoratif.
        """
        other = self.env["res.company"].create({"name": "Société voisine"})
        self.env.user.write({"company_ids": [Command.link(other.id)]})
        # Odoo 18 : un compte partagé porte un code PAR société, et la
        # contrainte se vérifie à la fin de l'écriture — les deux ensemble,
        # sous la société qui reçoit le compte.
        self.account_software.with_company(other).write(
            {"company_ids": [Command.link(other.id)], "code": self.account_software.code}
        )
        journal = self.env["account.journal"].create(
            {
                "name": "OD voisine",
                "code": "ODV",
                "type": "general",
                "company_id": other.id,
            }
        )
        payable = self.env["account.account"].with_company(other).create(
            {
                "name": "Fournisseurs voisins",
                "code": "200100",
                "account_type": "liability_payable",
                "reconcile": True,
            }
        )
        column = self.analytic_a.root_plan_id._column_name()
        move = self.env["account.move"].with_company(other).create(
            {
                "move_type": "entry",
                "journal_id": journal.id,
                "date": fields.Date.context_today(self.env.user),
                "company_id": other.id,
                "line_ids": [
                    Command.create(
                        {
                            "account_id": self.account_software.id,
                            "debit": 999.0,
                            "credit": 0.0,
                            "name": "Charge voisine",
                            "analytic_distribution": {str(self.analytic_a.id): 100.0},
                        }
                    ),
                    Command.create(
                        {
                            "account_id": payable.id,
                            "debit": 0.0,
                            "credit": 999.0,
                            "name": "Contrepartie voisine",
                        }
                    ),
                ],
            }
        )
        move.action_post()
        carried = self.env["account.analytic.line"].sudo().search(
            [(column, "=", self.analytic_a.id), ("company_id", "=", other.id)]
        )
        self.assertTrue(carried)

        budget = self._make_budget()
        line = budget.line_ids
        line.write({"analytic_account_ids": [Command.set(self.analytic_a.ids)]})
        self._post_bill(self.account_software, 100.0, analytic=self.analytic_a)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 100.0)

    def test_a_distributed_bill_on_another_account_stays_out(self):
        """Le filtre analytique RESTREINT le poste, il ne l'étend pas.

        Une facture ventilée sur le bon compte analytique mais passée sur un
        AUTRE compte du grand livre n'entre pas : la part est bien là, mais
        l'écriture n'est pas du poste.
        """
        budget = self._make_budget()
        line = budget.line_ids
        line.write({"analytic_account_ids": [Command.set(self.analytic_a.ids)]})
        self._post_bill(self.account_telecom, 400.0, analytic=self.analytic_a)
        self._post_bill(self.account_software, 100.0, analytic=self.analytic_a)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 100.0)

    def test_a_filtered_revenue_line_reads_positive(self):
        """Le signe suit le MODÈLE lu : l'analytique d'un produit est positive."""
        budget = self._make_budget(budget_type="revenue", positions=[self.position_revenue])
        line = budget.line_ids
        line.write({"analytic_account_ids": [Command.set(self.analytic_a.ids)]})
        self._post_revenue(900.0, analytic=self.analytic_a)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 900.0)

    def test_internal_cost_sees_only_analytic_lines_without_a_journal_item(self):
        """🔴 Le test qui empêche le double comptage.

        Une facture avec distribution analytique produit une écriture ET une ligne
        analytique. La ligne « coût interne » ne doit voir que ce que la
        comptabilité ne porte pas, sinon chaque dollar compterait deux fois.
        """
        self._post_bill(self.account_software, 800.0, analytic=self.analytic_a)
        self._internal_cost(self.analytic_a, 150.0)

        # La preuve que les deux mondes existent bien pour le même compte analytique.
        # ⚠️ La colonne se demande au plan : `account_id` n'est celle du premier
        # plan racine, et une base fraîche en porte déjà un.
        column = self.analytic_a.root_plan_id._column_name()
        analytic_lines = self.env["account.analytic.line"].search(
            [(column, "=", self.analytic_a.id)]
        )
        self.assertEqual(len(analytic_lines), 2)
        self.assertEqual(len(analytic_lines.filtered("move_line_id")), 1)

        budget = self.env["bf.budget"].create(
            {
                "name": "Deux sources",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        accounting = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 1000.0,
            }
        )
        internal = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "source": "internal_cost",
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 1000.0,
            }
        )
        budget.invalidate_recordset()
        self.assertEqual(accounting.amount_actual, 800.0)
        self.assertEqual(internal.amount_actual, 150.0)
        # Le total du budget est la somme, jamais 800 + 800 + 150.
        self.assertEqual(budget.amount_actual, 950.0)

        # 🔴 La contre-épreuve : c'est bien le garde `move_line_id = False` qui
        # discrimine, et rien d'autre. Sans lui, la ligne « coût interne » verrait
        # aussi la ligne analytique née de la facture, et les 800 $ seraient
        # comptés deux fois dans le même budget.
        naive = self.env["account.analytic.line"].search(
            [(column, "=", self.analytic_a.id)]
        )
        self.assertEqual(-sum(naive.mapped("amount")), 950.0)
        guarded = naive.filtered(lambda line: not line.move_line_id)
        self.assertEqual(-sum(guarded.mapped("amount")), 150.0)

    def test_committed_equals_actual_without_a_satellite(self):
        """Le socle ne connaît aucun engagement : il ne prétend pas le contraire."""
        budget = self._make_budget()
        self._post_bill(self.account_software, 500.0)
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_committed, budget.line_ids.amount_actual)

    def test_theoretical_follows_the_monthly_split(self):
        """🔴 Le théorique n'est pas un prorata du temps écoulé sur l'exercice.

        Tout le prévu est posé sur janvier : dès février, le théorique vaut le
        total, alors qu'un prorata du temps écoulé n'en donnerait qu'un douzième.
        """
        budget = self._make_budget(planned=1200.0)
        line = budget.line_ids
        january = line.period_ids.sorted("date_start")[0]
        for period in line.period_ids:
            period.amount_planned = 0.0
        january.amount_planned = 1200.0
        line.invalidate_recordset()
        self.assertEqual(line.amount_planned, 1200.0)
        self.assertEqual(line.theoretical_basis, "prorata")
        today = fields.Date.context_today(self.env.user)
        if today.month > 1:
            self.assertEqual(line.amount_theoretical, 1200.0)
        else:
            self.assertLess(line.amount_theoretical, 1200.0)

    def test_theoretical_is_zero_before_the_exercise(self):
        budget = self._make_budget()
        future = self.date_start.replace(year=self.date_start.year + 2)
        budget.write({"date_start": future, "date_end": future.replace(month=12, day=31)})
        budget.line_ids.action_regenerate_periods()
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_theoretical, 0.0)

    def test_alert_needs_both_the_percentage_and_the_floor(self):
        """Un pourcentage seul hurle sur les petits postes, un montant seul reste muet."""
        budget = self._make_budget(planned=1200.0, state="open")
        line = budget.line_ids
        # La ventilation vaut déjà 100 $ par mois : la création répartit le
        # total. ⚠️ La réécrire ici serait refusée depuis 18.0.1.2.1, et à
        # raison : la ventilation d'un budget OUVERT ne se retouche plus.
        # Dérive de 60 $ : bien au-delà de 10 %, mais sous le plancher de 250 $.
        self._post_bill(self.account_software, line.amount_theoretical + 60.0)
        line.invalidate_recordset()
        self.assertGreater(line.amount_drift, 0.0)
        self.assertFalse(line.is_alert)
        # Une dérive de 400 $ franchit les deux seuils.
        self._post_bill(self.account_software, 400.0)
        line.invalidate_recordset()
        self.assertTrue(line.is_alert)
        # Un dépassement assumé cesse d'être signalé, sans effacer la dérive.
        line.write({"overrun_accepted": True, "overrun_reason": "Renouvellement anticipé"})
        line.invalidate_recordset()
        self.assertFalse(line.is_alert)
        self.assertGreater(line.amount_drift, 0.0)

    def test_a_draft_budget_never_alerts(self):
        budget = self._make_budget(planned=120.0)
        self._post_bill(self.account_software, 9000.0)
        budget.line_ids.invalidate_recordset()
        self.assertFalse(budget.line_ids.is_alert)

    def test_coverage_reports_uncovered_and_duplicated_accounts(self):
        budget = self._make_budget(positions=[self.position_software])
        self.assertIn(self.account_telecom, budget.uncovered_account_ids)
        self.assertTrue(budget.coverage_warning)
        double = self.env["bf.budget.position"].create(
            {
                "name": "Logiciels bis",
                "code": "SOFT2",
                "budget_type": "expense",
                "company_id": self.company.id,
                "account_ids": [Command.set(self.account_software.ids)],
            }
        )
        self.env["bf.budget.line"].create(
            {"budget_id": budget.id, "position_id": double.id, "amount_planned": 10.0}
        )
        budget.invalidate_recordset()
        self.assertIn(self.account_software, budget.duplicated_account_ids)

    def test_report_rows_carry_the_month_and_the_cumulative(self):
        budget = self._make_budget(planned=1200.0, state="open")
        self._post_bill(self.account_software, 300.0)
        budget.invalidate_recordset()
        rows = budget._report_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["planned"], 1200.0)
        self.assertEqual(row["actual"], 300.0)
        self.assertEqual(row["month_planned"], 100.0)
        totals = budget._report_totals(rows)
        self.assertEqual(totals["planned"], 1200.0)

    def test_report_renders(self):
        """Le rapport se rend pour de vrai.

        ⚠️ En test, le moteur rend du HTML et non un PDF : on contrôle donc le
        contenu, pas le type de fichier.
        """
        budget = self._make_budget(planned=1200.0, state="open")
        self._post_bill(self.account_software, 300.0)
        budget.invalidate_recordset()
        # Le texte s'attend en anglais, la source : épinglé, parce qu'un contexte sans
        # langue retombe sur celle d'OdooBot (fr_CA sur une base francophone).
        self.env["res.lang"]._activate_lang("en_US")
        report = self.env["ir.actions.report"].with_context(lang="en_US")._render_qweb_html(
            "bf_budget.report_bf_budget", budget.ids
        )
        html = report[0].decode() if isinstance(report[0], bytes) else report[0]
        self.assertIn("Exercice test", html)
        self.assertIn("Licences logicielles", html)
        self.assertIn("Theoretical", html)

    def test_alert_filter_is_searchable(self):
        """⚠️ Un filtre de vue sur un champ calculé NON stocké fait échouer
        l'installation (« Unsearchable field ») si le champ n'a pas de `search=`.
        Le défaut est invisible à la lecture du code : il se prouve ici.
        """
        budget = self._make_budget(planned=1200.0, state="open")
        line = budget.line_ids
        # La ventilation vaut déjà 100 $ par mois : la création répartit le
        # total. ⚠️ La réécrire ici serait refusée depuis 18.0.1.2.1, et à
        # raison : la ventilation d'un budget OUVERT ne se retouche plus.
        self._post_bill(self.account_software, line.amount_theoretical + 900.0)
        line.invalidate_recordset()
        self.assertTrue(line.is_alert)
        found = self.env["bf.budget.line"].search([("is_alert", "=", True)])
        self.assertIn(line, found)
        quiet = self.env["bf.budget.line"].search([("is_alert", "=", False)])
        self.assertNotIn(line, quiet)

    def test_hours_logged_but_valued_at_zero_are_signalled(self):
        """🔴 Zéro dépensé, ou un taux horaire qui manque ? La ligne doit le dire.

        Odoo valorise le temps à partir du coût horaire de l'employé. Sans ce
        taux, `amount` vaut 0 alors que les heures sont bien saisies, et la ligne
        afficherait « rien dépensé » sans que rien ne paraisse anormal.
        """
        column = self.analytic_a.root_plan_id._column_name()
        self.env["account.analytic.line"].create(
            {
                "name": "Temps non valorisé",
                column: self.analytic_a.id,
                "amount": 0.0,
                "unit_amount": 7.5,
                "date": fields.Date.context_today(self.env.user),
                "company_id": self.company.id,
            }
        )
        budget = self.env["bf.budget"].create(
            {
                "name": "Main-d'oeuvre",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        line = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "source": "internal_cost",
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 5000.0,
            }
        )
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 0.0)
        self.assertTrue(line.has_unvalued_time)
        self.assertEqual(line.unvalued_hours, 7.5)

        # Du temps correctement valorisé ne déclenche rien.
        self._internal_cost(self.analytic_a, 400.0)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 400.0)
        self.assertTrue(line.has_unvalued_time)  # les 7,5 h sans taux restent signalées
