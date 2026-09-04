# Part of bf_recruitment_source_expense. Voir LICENSE.
"""Ce qu'on prouve ici.

1. Un débours s'impute au SITE, et choisir le site remplit le poste.
2. Le garde tient hors formulaire : la contrainte refuse le site d'un poste
   avec le poste d'un autre, là où l'`onchange` ne tourne pas.
3. Le coût par candidature d'un site ne compte QUE ses débours à lui.
4. Une dépense refusée n'est pas un débours, ici comme dans `bf_recruitment_expense`.
5. 🔴 Ce que les sites n'expliquent pas est écrit : la somme non imputée sort
   dans l'avertissement du poste, à côté des candidatures sans source.
6. Un site payé qui n'a rien rapporté est nommé, et n'a pas un coût par
   candidature de zéro.
"""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRecruitmentSourceExpense(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.job = cls.env["hr.job"].create({
            "name": "Développeuse",
            "company_id": cls.company.id,
            "is_published": True,
        })
        cls.other_job = cls.env["hr.job"].create({
            "name": "Chargée de projet",
            "company_id": cls.company.id,
        })
        cls.seek = cls.env["utm.source"].create({"name": "SEEK"})
        cls.indeed = cls.env["utm.source"].create({"name": "Indeed"})
        cls.source = cls.env["hr.recruitment.source"].create({
            "source_id": cls.seek.id, "job_id": cls.job.id,
        })
        cls.other_source = cls.env["hr.recruitment.source"].create({
            "source_id": cls.indeed.id, "job_id": cls.job.id,
        })
        cls.foreign_source = cls.env["hr.recruitment.source"].create({
            "source_id": cls.seek.id, "job_id": cls.other_job.id,
        })

        cls.product = cls.env["product.product"].create({
            "name": "Affichage",
            "can_be_expensed": True,
            "type": "service",
            "standard_price": 0.0,
        })
        cls.spender = cls.env["hr.employee"].create({
            "name": "Qui paie les annonces",
            "company_id": cls.company.id,
        })

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------

    def _make_expense(self, amount, source=None, job=None, refused=False):
        expense = self.env["hr.expense"].create({
            "name": "Affichage",
            "employee_id": self.spender.id,
            "product_id": self.product.id,
            "company_id": self.company.id,
            "job_id": (job or self.job).id,
            "recruitment_source_id": source.id if source else False,
            "total_amount_currency": amount,
        })
        if refused:
            sheet = self.env["hr.expense.sheet"].create({
                "name": "Note refusée",
                "employee_id": self.spender.id,
                "company_id": self.company.id,
                "expense_line_ids": [(6, 0, [expense.id])],
            })
            sheet._do_refuse("Hors budget")
            expense.invalidate_recordset(["state"])
            self.assertEqual(expense.state, "refused")
        return expense

    def _make_applicant(self, utm_source=None, job=None, name="Candidate", hired=False):
        candidate = self.env["hr.candidate"].create({
            "partner_name": name,
            "email_from": "candidate@example.invalid",
            "company_id": self.company.id,
        })
        values = {
            "candidate_id": candidate.id,
            "job_id": (job or self.job).id,
            "company_id": self.company.id,
        }
        if utm_source is not None:
            values["source_id"] = utm_source.id
        applicant = self.env["hr.applicant"].create(values)
        if hired:
            applicant.write({"date_closed": "2026-09-01 12:00:00"})
        return applicant

    # ------------------------------------------------------------------
    # 1. Le site sur le débours
    # ------------------------------------------------------------------

    def test_choosing_the_site_fills_the_job(self):
        expense = self.env["hr.expense"].new({
            "name": "Affichage",
            "employee_id": self.spender.id,
            "product_id": self.product.id,
            "company_id": self.company.id,
            "recruitment_source_id": self.source.id,
        })
        expense._onchange_recruitment_source_id()
        self.assertEqual(
            expense.job_id, self.job,
            "Saisir le site et le poste à la main, c'est deux occasions de se "
            "tromper pour une seule information.",
        )

    def test_a_site_from_another_job_is_refused(self):
        """🔴 Le garde que l'`onchange` ne peut pas poser.

        Un `onchange` ne tourne que dans un formulaire : une dépense créée par
        import ou par RPC ne le déclenche pas.
        """
        with self.assertRaises(ValidationError):
            self._make_expense(100.0, source=self.foreign_source, job=self.job)

    # ------------------------------------------------------------------
    # 2. Le coût par candidature
    # ------------------------------------------------------------------

    def test_a_site_carries_only_its_own_spend(self):
        self._make_expense(400.0, source=self.source)
        self._make_expense(600.0, source=self.other_source)
        self.source.invalidate_recordset()
        self.other_source.invalidate_recordset()
        self.assertEqual(self.source.expense_total, 400.0)
        self.assertEqual(self.other_source.expense_total, 600.0)

    def test_cost_per_applicant_divides_by_what_the_site_brought(self):
        self._make_expense(400.0, source=self.source)
        self._make_applicant(utm_source=self.seek, name="Une")
        self._make_applicant(utm_source=self.seek, name="Deux")
        self.source.invalidate_recordset()
        self.assertEqual(self.source.applicant_count, 2)
        self.assertEqual(self.source.cost_per_applicant, 200.0)

    def test_cost_per_hire_from_the_site_uses_its_own_hires(self):
        self._make_expense(900.0, source=self.source)
        self._make_applicant(utm_source=self.seek, name="Embauchée", hired=True)
        self._make_applicant(utm_source=self.seek, name="Pas embauchée")
        self.source.invalidate_recordset()
        self.assertEqual(self.source.hired_count, 1)
        self.assertEqual(self.source.cost_per_hire_from_source, 900.0)

    def test_a_refused_expense_is_not_a_spend(self):
        self._make_expense(500.0, source=self.source, refused=True)
        self.source.invalidate_recordset()
        self.assertEqual(self.source.expense_total, 0.0)

    def test_a_site_that_cost_and_brought_nothing_is_named(self):
        self._make_expense(750.0, source=self.source)
        self.source.invalidate_recordset()
        self.assertEqual(
            self.source.cost_per_applicant, 0.0,
            "Sans candidature il n'y a pas de coût par candidature.",
        )
        self.assertIn("n'a rapporté aucune candidature", self.source.stat_warning)

    def test_the_lot_five_warnings_survive_the_bridge(self):
        """⚠️ Le pont ÉTEND la liste de `bf_recruitment_source`, il ne la remplace pas."""
        self.job.is_published = False
        self._make_expense(300.0, source=self.source)
        self.source.invalidate_recordset()
        self.assertIn("n'est pas publié", self.source.stat_warning)

    # ------------------------------------------------------------------
    # 3. 🔴 Ce que les sites n'expliquent pas
    # ------------------------------------------------------------------

    def test_the_job_splits_attributed_from_unattributed_spend(self):
        self._make_expense(400.0, source=self.source)
        self._make_expense(250.0)
        self.job.invalidate_recordset()
        self.assertEqual(self.job.attributed_expense_total, 400.0)
        self.assertEqual(self.job.unattributed_expense_total, 250.0)

    def test_the_unattributed_spend_is_written_in_the_warning(self):
        self._make_expense(400.0, source=self.source)
        self._make_expense(250.0)
        self.job.invalidate_recordset()
        self.assertIn(
            "aucun site", self.job.source_warning,
            "La somme qu'aucun site ne porte doit s'écrire, sinon le coût par "
            "candidature se lit comme s'il couvrait tout.",
        )
        self.assertIn("250", self.job.source_warning)

    def test_nothing_unattributed_writes_nothing(self):
        self._make_expense(400.0, source=self.source)
        self.job.invalidate_recordset()
        self.assertNotIn("aucun site", self.job.source_warning or "")

    def test_the_lot_five_job_warning_survives_the_bridge(self):
        """La candidature sans source et le débours sans site sont le même
        écart, vu par ses deux bouts. Les deux phrases coexistent."""
        self._make_expense(250.0)
        self._make_applicant(utm_source=None, name="Sans source")
        self.job.invalidate_recordset()
        self.assertIn("n'ont aucune source", self.job.source_warning)
        self.assertIn("aucun site", self.job.source_warning)
