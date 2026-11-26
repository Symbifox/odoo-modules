from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import BfBudgetSubscriptionCommon


@tagged("post_install", "-at_install")
class TestLinkWizard(BfBudgetSubscriptionCommon):
    def _wizard(self, **extra):
        vals = {"company_id": self.company.id, "only_unassigned": True}
        vals.update(extra)
        # L'aperçu s'attend en anglais, la source : épinglé, parce qu'un
        # contexte sans langue retombe sur celle d'OdooBot (fr_CA sur une base francophone).
        self.env["res.lang"]._activate_lang("en_US")
        return self.env["bf.budget.subscription.link.wizard"].with_context(lang="en_US").create(vals)

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
        self.assertIn("no posted bill", wizard.preview)
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_an_account_in_no_position_is_reported_not_guessed(self):
        orphelin = self.env["account.account"].with_company(self.company).create(
            {"name": "Charge orpheline", "code": "699000", "account_type": "expense"}
        )
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, orphelin, 100.0, self.date_start)
        wizard = self._wizard()
        self.assertIn("is in no budget item", wizard.preview)
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
        self.assertIn("to be settled by hand", wizard.preview)
        with self.assertRaises(UserError):
            wizard.action_apply()
        self.assertFalse(sub.budget_position_id)

    def test_it_leaves_the_already_attached_alone_by_default(self):
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, self.account_software, 100.0, self.date_start)
        sub.budget_position_id = self.position_telecom
        wizard = self._wizard()
        self.assertEqual(wizard.preview, "No subscription to review.")
        wizard.only_unassigned = False
        wizard.invalidate_recordset()
        self.assertIn("✅", wizard.preview)

    def test_l_apercu_se_lit_dans_la_langue_de_l_usager(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        sub = self._make_subscription("monthly", 100.0)
        sub.budget_position_id = self.position_telecom
        wizard = self._wizard().with_context(lang="fr_CA")
        wizard.invalidate_recordset()
        self.assertEqual(wizard.preview, "Aucun abonnement à examiner.")


@tagged("post_install", "-at_install")
class TestLinkWizardAsARealRole(BfBudgetSubscriptionCommon):
    """🔴 Le garde qui manquait, et que la production a trouvé à ma place.

    Les tests tournent en superutilisateur, qui contourne toutes les listes de
    contrôle : l'assistant passait sur une base d'essai et mourait en `AccessError` dès qu'un
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
        # l'assistant écrit dans le registre, il demande donc le registre.
        cls.budget_manager_registre = new_test_user(
            cls.env,
            login="qa_budget_abo_registre",
            groups="base.group_user,bf_budget.group_bf_budget_manager,"
                   "bf_subscription.group_subscription_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )

    def test_the_wizard_runs_under_a_budget_manager(self):
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, self.account_software, 100.0, self.date_start)
        wizard = (
            self.env["bf.budget.subscription.link.wizard"]
            .with_user(self.budget_manager_registre)
            .create({"company_id": self.company.id, "only_unassigned": True})
        )
        self.assertIn("✅", wizard.preview)
        wizard.action_apply()
        self.assertEqual(sub.budget_position_id, self.position_software)

    def test_the_wizard_refuses_a_budget_manager_without_the_register(self):
        """🔴 l'aperçu nomme chaque abonnement et l'application écrit
        dans le registre, le tout en sudo. Sans le registre, l'assistant
        refuse avant de rien relever, et son menu n'apparaît pas."""
        sub = self._make_subscription("monthly", 100.0)
        self._vendor_bill(sub, self.account_software, 100.0, self.date_start)
        wizard = (
            self.env["bf.budget.subscription.link.wizard"]
            .with_user(self.budget_manager)
            .create({"company_id": self.company.id, "only_unassigned": True})
        )
        with self.assertRaises(AccessError):
            wizard.preview  # noqa: B018
        with self.assertRaises(AccessError):
            wizard.action_apply()
        self.assertFalse(sub.budget_position_id)
        menu = self.env.ref("bf_budget_subscription.menu_bf_budget_subscription_link")
        visibles = self.env["ir.ui.menu"].with_user(self.budget_manager)._visible_menu_ids()
        self.assertNotIn(menu.id, visibles)

    def test_a_budget_manager_reads_the_line_without_accounting_rights(self):
        """Le formulaire ne casse pas, et les sommes sont là, sans le registre.

        🔴 Ce gestionnaire n'a ni la comptabilité ni le registre (`base.group_user`
        ne l'implique plus). Le montant des engagements se
        calcule quand même : hors sudo, lire le calendrier de l'abonnement
        levait une erreur d'accès et la ligne ne s'ouvrait plus."""
        sub = self._make_subscription("monthly", 100.0)
        sub.budget_position_id = self.position_software
        budget = self._make_budget(planned=1200.0)
        line = budget.line_ids.with_user(self.budget_manager)
        line.invalidate_recordset()
        self.assertEqual(line.subscription_count, 1)
        self.assertEqual(line.subscription_period_total, 1200.0)

    def test_the_register_no_longer_comes_with_employee_status(self):
        """`base.group_user` n'implique plus le registre. Ce module a
        donc enfin des usagers que ses gardes refusent — ce gestionnaire en
        est un, et le registre lui reste fermé."""
        sub = self._make_subscription("monthly", 100.0)
        self.assertNotIn(
            self.env.ref("bf_subscription.group_subscription_user"),
            self.env.ref("base.group_user").implied_ids,
        )
        self.assertFalse(
            self.budget_manager.has_group("bf_subscription.group_subscription_user")
        )
        with self.assertRaises(AccessError):
            sub.with_user(self.budget_manager).read(["name"])
