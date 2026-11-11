from datetime import timedelta

from odoo import Command
from odoo.tests import tagged

from ..models.bf_budget_line import analytic_share
from .common import BfBudgetPurchaseCommon


@tagged("post_install", "-at_install")
class TestPurchaseCommitments(BfBudgetPurchaseCommon):
    def _fresh(self, line):
        line.invalidate_recordset()
        self.env.invalidate_all()
        return line

    def test_confirmed_unbilled_po_is_committed(self):
        line = self._line()
        self._po(300.0)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 300.0)
        self.assertEqual(line.purchase_committed_count, 1)
        self.assertEqual(line.amount_actual, 0.0)
        self.assertEqual(line.amount_committed, 300.0)

    def test_rfq_is_not_committed(self):
        """Une demande de prix n'est pas une décision : personne n'a commandé."""
        line = self._line()
        self._po(300.0, confirm=False)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)
        self.assertEqual(line.amount_committed, 0.0)

    def test_cancelled_order_is_not_committed(self):
        order = self._po(300.0)
        order.button_cancel()
        line = self._line()
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)

    def test_posted_bill_moves_the_amount_without_changing_the_total(self):
        """La facture comptabilisée change la CASE du montant, pas le total engagé."""
        line = self._line()
        order = self._po(300.0)
        line = self._fresh(line)
        self.assertEqual(line.amount_committed, 300.0)
        self._bill(order)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)
        self.assertEqual(line.amount_actual, 300.0)
        self.assertEqual(line.amount_committed, 300.0)

    def test_draft_bill_keeps_the_commitment(self):
        """🔴 Le piège `qty_invoiced` : Odoo compte le facturé dès le BROUILLON.

        S'y fier ferait disparaître l'engagement à la saisie du brouillon,
        avant que le réalisé ne le reprenne : le montant sortirait du budget le
        temps de la comptabilisation. Le garde est l'écriture COMPTABILISÉE.
        """
        line = self._line()
        order = self._po(300.0)
        self._bill(order, post=False)
        line = self._fresh(line)
        self.assertEqual(order.order_line.qty_invoiced, 1.0)  # le piège est réel
        self.assertEqual(line.purchase_committed, 300.0)
        self.assertEqual(line.amount_actual, 0.0)
        self.assertEqual(line.amount_committed, 300.0)

    def test_partial_billing_leaves_the_rest(self):
        line = self._line()
        order = self._po(100.0, qty=3.0)
        order.action_create_invoice()
        bill = order.invoice_ids
        bill.invoice_date = self.today
        bill.invoice_line_ids.filtered("purchase_line_id").quantity = 1.0
        bill.action_post()
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 200.0)
        self.assertEqual(line.amount_actual, 100.0)
        self.assertEqual(line.amount_committed, 300.0)

    def test_credit_note_returns_its_share(self):
        """Un avoir comptabilisé rend sa part à l'engagé : la commande tient toujours."""
        line = self._line()
        order = self._po(300.0)
        bill = self._bill(order)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)
        self._refund(bill)
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 0.0)
        self.assertEqual(line.purchase_committed, 300.0)
        self.assertEqual(line.amount_committed, 300.0)

    def test_overbilled_po_does_not_go_negative(self):
        """Une facture au-delà du bon de commande ne crée pas d'engagement négatif.

        Le dépassement est dans le réalisé, et c'est là qu'il doit se voir : un
        reste à facturer négatif le maquillerait.
        """
        line = self._line()
        order = self._po(300.0)
        self._bill(order, price=400.0)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)
        self.assertEqual(line.amount_actual, 400.0)
        self.assertEqual(line.amount_committed, 400.0)

    def test_dates_outside_the_budget_are_not_this_budgets_commitment(self):
        line = self._line()
        # Avant le budget et après lui : ni l'une ni l'autre n'y tombe.
        self._po(300.0, date=self.date_start - (self.date_end - self.date_start) / 10)
        self._po(500.0, date=self.date_end + (self.date_end - self.date_start) / 10)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)
        self.assertEqual(line.purchase_period_total, 0.0)

    def test_another_companys_order_does_not_count(self):
        other = self.env["res.company"].create({"name": "Autre société commandes"})
        self.env.user.write({"company_ids": [Command.link(other.id)]})
        self.product_software.product_tmpl_id.with_company(
            other
        ).property_account_expense_id = (
            self.env["account.account"]
            .with_company(other)
            .create({"name": "Licences ailleurs", "code": "602199",
                     "account_type": "expense"})
        )
        self._po(300.0, company=other)
        line = self._line()
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)

    def test_the_account_of_the_product_routes_the_order(self):
        """Le poste est celui où la FACTURE tombera : le compte du produit décide."""
        budget = self.env["bf.budget"].create(
            {
                "name": "Exercice deux postes",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        line_soft = self._line(budget=budget)
        line_tel = self._line(budget=budget, position=self.position_telecom)
        self._po(300.0, product=self.product_telecom)
        line_soft = self._fresh(line_soft)
        self.assertEqual(line_soft.purchase_committed, 0.0)
        self.assertEqual(line_tel.purchase_committed, 300.0)

    def test_analytic_restriction_filters_orders(self):
        line = self._line(analytic_account_ids=[Command.set(self.analytic_a.ids)])
        self._po(100.0, analytic=self.analytic_a)
        self._po(200.0, analytic=self.analytic_b)
        self._po(400.0)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 100.0)

    def test_analytic_share_reads_composite_keys_once(self):
        """⚠️ Une clé peut réunir plusieurs comptes (« 3,7 ») : elle porte UN
        pourcentage, compté UNE fois même quand la ligne en surveille deux ; et
        la clé entière comparée à un identifiant manquerait ces lignes-là."""
        self.assertEqual(analytic_share({"3,7": 100.0}, [7]), 1.0)
        self.assertEqual(analytic_share({"3,7": 100.0}, [3, 7]), 1.0)
        self.assertEqual(analytic_share({"7": 40.0, "3": 60.0}, [7]), 0.4)
        self.assertEqual(analytic_share({"37": 100.0}, [7]), 0.0)
        self.assertEqual(analytic_share({}, [7]), 0.0)
        self.assertEqual(analytic_share(None, [7]), 0.0)

    def test_the_key_is_split_like_the_core_splits_it(self):
        """🔴 Le cœur casse la clé sur TOUT ce qui n'est pas un chiffre.

        À la recherche : `regexp_split_to_array(…, '\\D+')`
        (`analytic.mixin._query_analytic_accounts`). À la création des lignes
        analytiques : `map(int, account_ids.split(","))`, et `int()` tolère
        l'espace (`account.move.line._prepare_analytic_distribution_line`).
        Une clé « 3, 7 » écrite à la main (import, RPC, correctif de données)
        est donc comptée par le cœur, et le réalisé de la ligne qui surveille
        7 la porte. Un `split(",")` ici laissait « 7 » introuvable : l'engagé
        tombait à zéro, puis le montant RÉAPPARAISSAIT au geste de
        comptabilisation — l'invariant publié devenait faux sur cette
        distribution-là.
        """
        self.assertEqual(analytic_share({"3, 7": 100.0}, [7]), 1.0)
        self.assertEqual(analytic_share({"3, 7": 100.0}, [3]), 1.0)
        self.assertEqual(analytic_share({"3 , 7": 40.0}, [7]), 0.4)
        # Et ce qui ne doit PAS matcher continue de ne pas matcher : un
        # découpage trop large compterait une part étrangère.
        self.assertEqual(analytic_share({"37": 100.0}, [7]), 0.0)
        self.assertEqual(analytic_share({"3, 17": 100.0}, [7]), 0.0)

    def test_a_shared_order_commits_its_share_once(self):
        """🔴 L'engagé d'une ligne restreinte est la PART, pas le plein reste.

        Une commande 1 000 $ ventilée A:50/B:50 : la ligne qui surveille A
        engage 500, celle qui surveille B engage 500 — jamais 1 000 chacune. À
        la comptabilisation, le réalisé pondéré reprend exactement ces parts :
        l'engagé + réalisé de chaque ligne NE BOUGE PAS, il change de case. Le
        calendrier (dû/total) porte la même part.
        """
        budget = self.env["bf.budget"].create(
            {
                "name": "Parts",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        line_a = self._line(
            budget=budget, analytic_account_ids=[Command.set(self.analytic_a.ids)]
        )
        line_b = self._line(
            budget=budget, analytic_account_ids=[Command.set(self.analytic_b.ids)]
        )
        order = self._po(
            1000.0,
            distribution={self.analytic_a: 50.0, self.analytic_b: 50.0},
        )
        line_a, line_b = self._fresh(line_a), self._fresh(line_b)
        self.assertEqual(line_a.purchase_committed, 500.0)
        self.assertEqual(line_b.purchase_committed, 500.0)
        self.assertEqual(line_a.purchase_period_total, 500.0)
        self.assertEqual(line_a.amount_committed, 500.0)

        self._bill(order)
        line_a, line_b = self._fresh(line_a), self._fresh(line_b)
        self.assertEqual(line_a.purchase_committed, 0.0)
        self.assertEqual(line_a.amount_actual, 500.0)
        # L'invariant : le total engagé de la ligne n'a pas bougé.
        self.assertEqual(line_a.amount_committed, 500.0)
        self.assertEqual(line_b.amount_committed, 500.0)

    def test_a_downpayment_line_is_not_a_commitment(self):
        """Un acompte annonce les mêmes dollars que les lignes réelles de sa
        commande : le compter les compterait deux fois."""
        order = self._po(300.0)
        order.button_cancel()
        order.button_draft()
        self.env["purchase.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product_software.id,
                "product_qty": 1.0,
                "price_unit": 50.0,
                "date_planned": self._noon(self.today),
                "taxes_id": [Command.clear()],
                "is_downpayment": True,
            }
        )
        order.button_confirm()
        line = self._line()
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 300.0)

    def test_the_window_edge_is_the_budget_not_the_search_margin(self):
        """La recherche du bassin porte une marge de 2 jours (fuseau) : c'est le
        filtre fin par date convertie qui tient la frontière, pas elle."""
        line = self._line()
        self._po(300.0, date=self.date_end + timedelta(days=1))
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)
        self.assertEqual(line.purchase_period_total, 0.0)

    def test_an_internal_cost_line_with_a_position_ignores_purchases(self):
        """La garde est la SOURCE de la ligne, pas l'absence de poste : une
        ligne de coût interne qui porte aussi un poste et le bon axe analytique
        ne doit rien prendre des achats."""
        self._po(300.0, analytic=self.analytic_a)
        line = self._line(
            source="internal_cost",
            analytic_account_ids=[Command.set(self.analytic_a.ids)],
        )
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)

    def test_a_revenue_budget_ignores_purchases_even_on_matching_accounts(self):
        """La garde est le TYPE du budget, pas le hasard des comptes. Le socle
        refuse un compte de charge sur un poste de revenus, mais le domaine du
        cœur PERMET un compte de produits comme « compte de charge » d'un
        produit : c'est ce chemin-là, légitime quoique tordu, que la garde
        ferme."""
        product = self.env["product.product"].create(
            {
                "name": "Produit au compte de produits",
                "type": "service",
                "purchase_ok": True,
                "purchase_method": "purchase",
                "list_price": 0.0,
            }
        )
        product.product_tmpl_id.with_company(
            self.company
        ).property_account_expense_id = self.account_revenue
        budget = self._make_budget(
            budget_type="revenue", positions=[self.position_revenue]
        )
        self._po(300.0, product=product)
        line = self._fresh(budget.line_ids)
        self.assertEqual(line.purchase_committed, 0.0)
        self.assertEqual(line.purchase_period_total, 0.0)

    def test_foreign_currency_converts_to_the_budget_currency(self):
        line = self._line()
        self._po(200.0, currency=self.foreign_currency)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 100.0)

    def test_internal_cost_and_revenue_lines_ignore_purchases(self):
        self._po(300.0)
        internal = self._line(
            source="internal_cost",
            position_id=False,
            analytic_account_ids=[Command.set(self.analytic_a.ids)],
        )
        internal = self._fresh(internal)
        self.assertEqual(internal.purchase_committed, 0.0)
        revenue_budget = self._make_budget(
            budget_type="revenue", positions=[self.position_revenue]
        )
        revenue_line = self._fresh(revenue_budget.line_ids)
        self.assertEqual(revenue_line.purchase_committed, 0.0)
