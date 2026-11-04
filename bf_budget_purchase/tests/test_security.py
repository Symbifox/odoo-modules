from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import BfBudgetPurchaseCommon


@tagged("post_install", "-at_install")
class TestPurchaseBudgetSecurity(BfBudgetPurchaseCommon):
    """⚠️ Sous de VRAIS comptes, jamais uid 1, et sans lecture sudo avant.

    Le cache de l'ORM est par transaction : un montant lu d'abord en sudo serait
    resservi tel quel au compte sans droits, et le test passerait sans rien
    prouver. Chaque test lit donc sous le rôle EN PREMIER, et compare à un
    chiffre écrit en dur.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reader = new_test_user(
            cls.env,
            login="budget_purchase_reader",
            groups="base.group_user,bf_budget.group_bf_budget_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )

    def test_a_budget_reader_sees_the_committed_amount_without_purchase_rights(self):
        """🔴 Sans sudo, un gestionnaire de budget sans droit d'achat lirait
        zéro, et un engagé à zéro a l'air d'un poste sans commande en cours."""
        line = self._line()
        self._po(300.0)
        line.invalidate_recordset()
        self.env.invalidate_all()
        as_reader = line.with_user(self.reader)
        self.assertEqual(as_reader.purchase_committed, 300.0)
        self.assertEqual(as_reader.amount_committed, 300.0)
        self.assertEqual(as_reader.purchase_committed_count, 1)

    def test_the_orders_stay_behind_purchase_rights(self):
        """Le bouton caché ne protège que l'écran : la méthode, appelable par
        RPC, rendrait la liste des ids de commandes du poste. Elle refuse
        d'abord à qui ne peut pas lire les commandes."""
        line = self._line()
        self._po(300.0)
        with self.assertRaises(AccessError):
            line.with_user(self.reader).action_view_purchase_orders()

    def test_the_line_form_loads_for_a_budget_reader(self):
        views = self.env["bf.budget.line"].with_user(self.reader).get_views([(False, "form")])
        arch = views["views"]["form"]["arch"]
        self.assertIn("purchase_committed", arch)
        # Le bouton qui ouvre les commandes est réservé à qui peut les voir.
        self.assertNotIn("action_view_purchase_orders", arch)
        line = self._line()
        line.invalidate_recordset()
        self.env.invalidate_all()
        values = line.with_user(self.reader).read(
            ["purchase_committed", "purchase_committed_count",
             "purchase_period_total", "amount_committed"]
        )
        self.assertEqual(values[0]["purchase_committed"], 0.0)
