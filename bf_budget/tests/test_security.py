from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestBfBudgetSecurity(BfBudgetCommon):
    """⚠️ Les vues et les droits se contrôlent sous de VRAIS comptes.

    Jamais sous uid 1, qui n'a aucun groupe et voit tout. Et jamais après une
    lecture en `sudo` dans la même transaction : le cache de l'ORM est par
    transaction, un affichage privilégié fausse tout parcours par rôle.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reader = new_test_user(
            cls.env,
            login="budget_reader",
            groups="base.group_user,bf_budget.group_bf_budget_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )
        cls.manager = new_test_user(
            cls.env,
            login="budget_manager",
            groups="base.group_user,bf_budget.group_bf_budget_manager",
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )

    def test_reader_reads_but_does_not_write(self):
        budget = self._make_budget()
        as_reader = budget.with_user(self.reader)
        self.assertEqual(as_reader.name, "Exercice test")
        with self.assertRaises(AccessError):
            as_reader.write({"name": "Renommé"})

    def test_reader_does_not_create(self):
        with self.assertRaises(AccessError):
            self.env["bf.budget"].with_user(self.reader).create(
                {
                    "name": "Interdit",
                    "company_id": self.company.id,
                    "date_start": self.date_start,
                    "date_end": self.date_end,
                }
            )

    def test_manager_creates_and_opens(self):
        budget = (
            self.env["bf.budget"]
            .with_user(self.manager)
            .create(
                {
                    "name": "Par le gestionnaire",
                    "company_id": self.company.id,
                    "date_start": self.date_start,
                    "date_end": self.date_end,
                }
            )
        )
        self.env["bf.budget.line"].with_user(self.manager).create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "amount_planned": 500.0,
            }
        )
        budget.action_open()
        self.assertEqual(budget.state, "open")

    def test_a_budget_reader_sees_totals_without_the_ledger(self):
        """Choix assumé : la lecture du réel se fait en `sudo`.

        Un responsable de budget doit voir ses totaux sans qu'on lui ouvre le
        grand livre. ⚠️ Le corollaire vaut d'être écrit : tant qu'aucune action
        de dépliage vers les écritures n'existe, l'agrégat ne révèle rien de
        nominatif. Le jour où un satellite en ajoutera une, elle devra respecter
        les droits sur `account.move.line` et sur `hr.employee`.
        """
        budget = self._make_budget(state="open")
        self._post_bill(self.account_software, 640.0)
        as_reader = budget.with_user(self.reader)
        as_reader.invalidate_recordset()
        self.assertEqual(as_reader.amount_actual, 640.0)
        with self.assertRaises(AccessError):
            self.env["account.move.line"].with_user(self.reader).search([], limit=1).mapped(
                "balance"
            )

    def test_views_load_for_each_role(self):
        for account in (self.reader, self.manager):
            for view in ("list", "form"):
                self.env["bf.budget"].with_user(account).get_view(view_type=view)
                self.env["bf.budget.line"].with_user(account).get_view(view_type=view)
            self.env["bf.budget.position"].with_user(account).get_view(view_type="list")
