from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BfBudgetSubscriptionCommon


@tagged("post_install", "-at_install")
class TestLinkWizard(BfBudgetSubscriptionCommon):
    def _wizard(self, **extra):
        vals = {"company_id": self.company.id, "only_unassigned": True}
        vals.update(extra)
        return self.env["bf.budget.subscription.link.wizard"].create(vals)

    def test_it_deduces_the_position_from_the_real_bills(self):
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, self.account_software, 100.0, self.date_start)
        wizard = self._wizard()
        self.assertIn("✅", wizard.preview)
        wizard.action_apply()
        self.assertEqual(sub.budget_position_id, self.position_software)

    def test_the_dominant_account_wins_not_the_first_one(self):
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, self.account_telecom, 10.0, self.date_start)
        self._vendor_bill(sub, self.account_software, 900.0, self.date_start)
        self._wizard().action_apply()
        self.assertEqual(sub.budget_position_id, self.position_software)

    def test_a_subscription_without_a_bill_is_left_alone(self):
        self._make_subscription("monthly", 100.0)
        wizard = self._wizard()
        self.assertIn("aucune facture", wizard.preview)
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_an_account_in_no_position_is_reported_not_guessed(self):
        orphelin = self.env["account.account"].with_company(self.company).create(
            {"name": "Charge orpheline", "code": "699000", "account_type": "expense"}
        )
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, orphelin, 100.0, self.date_start)
        wizard = self._wizard()
        self.assertIn("n'est dans aucun poste", wizard.preview)
        with self.assertRaises(UserError):
            wizard.action_apply()
        self.assertFalse(sub.budget_position_id)

    def test_an_ambiguous_account_is_never_guessed(self):
        """🔴 Un rattachement deviné au hasard fausserait un budget sans que
        personne ne puisse le retrouver."""
        self.env["bf.budget.position"].create({
            "name": "Logiciels bis", "code": "SOFT2", "budget_type": "expense",
            "company_id": self.company.id,
            "account_ids": [Command.set(self.account_software.ids)],
        })
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, self.account_software, 100.0, self.date_start)
        wizard = self._wizard()
        self.assertIn("à trancher à la main", wizard.preview)
        with self.assertRaises(UserError):
            wizard.action_apply()
        self.assertFalse(sub.budget_position_id)

    def test_it_leaves_the_already_attached_alone_by_default(self):
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, self.account_software, 100.0, self.date_start)
        sub.budget_position_id = self.position_telecom
        wizard = self._wizard()
        self.assertEqual(wizard.preview, "Aucun abonnement à examiner.")
        wizard.only_unassigned = False
        wizard.invalidate_recordset()
        self.assertIn("✅", wizard.preview)


@tagged("post_install", "-at_install")
class TestLinkWizardAsARealRole(BfBudgetSubscriptionCommon):
    """🔴 Le garde qui manquait, et que la production a trouvé à ma place.

    Les tests tournent en superutilisateur, qui contourne toutes les listes de
    contrôle : l'assistant passait au banc et mourait en `AccessError` dès qu'un
    vrai gestionnaire de budget l'ouvrait. Un gestionnaire de budget n'a aucun
    droit sur le registre des abonnements ni sur la comptabilité, et il n'a pas
    à en avoir.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.tests import new_test_user
        cls.budget_manager = new_test_user(
            cls.env,
            login="qa_budget_abo",
            groups="base.group_user,bf_budget.group_bf_budget_manager",
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )

    def test_the_wizard_runs_under_a_budget_manager(self):
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, self.account_software, 100.0, self.date_start)
        wizard = (
            self.env["bf.budget.subscription.link.wizard"]
            .with_user(self.budget_manager)
            .create({"company_id": self.company.id, "only_unassigned": True})
        )
        self.assertIn("✅", wizard.preview)
        wizard.action_apply()
        self.assertEqual(sub.budget_position_id, self.position_software)

    def test_a_budget_manager_reads_the_line_without_accounting_rights(self):
        """Le champ affiché sur la ligne ne doit pas casser le formulaire."""
        sub = self._make_subscription("monthly", 100.0)
        sub.budget_position_id = self.position_software
        budget = self._make_budget(planned=1200.0)
        line = budget.line_ids.with_user(self.budget_manager)
        line.invalidate_recordset()
        self.assertEqual(line.subscription_count, 1)
        self.assertEqual(line.subscription_ids.mapped("name"), sub.mapped("name"))
        self.assertGreater(line.subscription_period_total, 0.0)

    def test_every_internal_user_already_reads_the_subscription_register(self):
        """⚠️ Constat mesuré, pas une hypothèse : `bf_subscription` fait
        impliquer son groupe « Utilisateur » par `base.group_user`. Tout
        utilisateur interne lit donc les abonnements, et ce module n'a AUCUNE
        règle d'accès à ajouter. Le test existe pour que la redondance ne soit
        pas réintroduite par réflexe."""
        sub = self._make_subscription("monthly", 100.0)
        self.assertTrue(
            self.budget_manager.has_group("bf_subscription.group_subscription_user")
        )
        self.assertEqual(sub.with_user(self.budget_manager).name, sub.name)
