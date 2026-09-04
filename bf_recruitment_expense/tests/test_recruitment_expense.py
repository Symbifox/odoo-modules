# Part of bf_recruitment_expense. Voir LICENSE.
"""Ce qu'on prouve ici.

1. Les heures de panel sortent du cahier d'entrevues, sans aucune saisie, et
   seules les séances TENUES comptent.
2. Le taux suit l'ordre annoncé : l'employé de la société du poste d'abord, le
   repli de la société ensuite, et rien du tout en dernier recours.
3. Une heure qu'aucun taux ne couvre est NOMMÉE, jamais comptée pour zéro.
4. Les débours suivent le poste, la clé analytique descend du poste à la
   dépense, et une distribution posée à la main n'est jamais écrasée.
5. Le coût par embauche n'existe pas tant qu'il n'y a pas d'embauche.
6. Les champs qui dérivent du salaire sont hors de portée d'un recruteur, et
   la paire le prouve : le gestionnaire RH, lui, les lit.
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRecruitmentExpense(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.other_company = cls.env["res.company"].create({"name": "Ailleurs inc."})
        cls.company.recruitment_panel_hourly_cost = 0.0

        cls.job = cls.env["hr.job"].create({
            "name": "Analyste d'affaires",
            "company_id": cls.company.id,
        })
        cls.guide = cls.env["bf.interview.guide"].create({
            "name": "Analyste d'affaires - tour 1",
            "round_type": "technique",
            "scale_max": 5,
            "company_id": cls.company.id,
            "criterion_ids": [
                (0, 0, {"name": "Cadrage du besoin", "weight": 1.0, "sequence": 10}),
            ],
        })
        cls.guide.action_publish()

        # Trois membres de panel, qui couvrent les trois chemins du taux.
        cls.paid_user = cls._make_user("panel_paye")
        cls.paid_employee = cls.env["hr.employee"].create({
            "name": "Membre payé",
            "user_id": cls.paid_user.id,
            "company_id": cls.company.id,
            "hourly_cost": 50.0,
        })
        cls.free_user = cls._make_user("panel_sans_employe")
        cls.elsewhere_user = cls._make_user("panel_ailleurs")
        cls.elsewhere_employee = cls.env["hr.employee"].create({
            "name": "Membre d'une autre société",
            "user_id": cls.elsewhere_user.id,
            "company_id": cls.other_company.id,
            "hourly_cost": 90.0,
        })

        cls.expense_product = cls.env["product.product"].create({
            "name": "Affichage de poste",
            "can_be_expensed": True,
            "type": "service",
            "standard_price": 0.0,
        })
        cls.spender = cls.env["hr.employee"].create({
            "name": "Qui dépense",
            "company_id": cls.company.id,
        })

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------

    @classmethod
    def _make_user(cls, login):
        return cls.env["res.users"].create({
            "name": login,
            "login": login,
            "email": "%s@example.invalid" % login,
            "company_id": cls.company.id,
            "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr_recruitment.group_hr_recruitment_interviewer").id,
            ])],
        })

    def _make_applicant(self, name="Camille Sans-Nom"):
        candidate = self.env["hr.candidate"].create({
            "partner_name": name,
            "email_from": "camille@example.invalid",
            "company_id": self.company.id,
        })
        return self.env["hr.applicant"].create({
            "candidate_id": candidate.id,
            "job_id": self.job.id,
            "company_id": self.company.id,
        })

    def _make_interview(self, panelists, duration=2.0, held=True, applicant=None):
        interview = self.env["bf.interview"].create({
            "applicant_id": (applicant or self._make_applicant()).id,
            "guide_id": self.guide.id,
            "company_id": self.company.id,
            "duration": duration,
            "interviewer_ids": [(6, 0, [u.id for u in panelists])],
            "date_start": "2026-03-04 14:00:00",
        })
        if held:
            interview.rating_line_ids.sudo().write({"score": 4, "state": "depose"})
            interview.action_mark_held()
        return interview

    def _make_expense(self, amount, refused=False):
        expense = self.env["hr.expense"].create({
            "name": "Annonce",
            "employee_id": self.spender.id,
            "product_id": self.expense_product.id,
            "company_id": self.company.id,
            "job_id": self.job.id,
            "total_amount_currency": amount,
        })
        if refused:
            # ⚠️ `hr.expense` n'a pas de refus à lui. L'état `refused` est
            # CALCULÉ depuis la feuille de frais : il vaut « refusée » quand la
            # feuille est annulée. Passer par la feuille est donc le seul moyen
            # d'obtenir l'état qu'on veut éprouver.
            sheet = self.env["hr.expense.sheet"].create({
                "name": "Note refusée",
                "employee_id": self.spender.id,
                "company_id": self.company.id,
                "expense_line_ids": [(6, 0, [expense.id])],
            })
            sheet._do_refuse("Hors budget")
            expense.invalidate_recordset(["state"])
            self.assertEqual(
                expense.state, "refused",
                "Le montage du test n'a pas produit l'état qu'il prétend.",
            )
        return expense

    # ------------------------------------------------------------------
    # 1. Les heures
    # ------------------------------------------------------------------

    def test_panel_hours_multiply_duration_by_panel_size(self):
        """Deux personnes, deux heures : quatre heures-personnes, sans saisie."""
        self._make_interview([self.paid_user, self.free_user], duration=2.0)
        self.assertEqual(self.job.panel_hours, 4.0)

    def test_only_held_sessions_cost_time(self):
        """Une séance planifiée n'a rien coûté ; une séance annulée non plus."""
        self._make_interview([self.paid_user], duration=3.0, held=False)
        self.assertEqual(
            self.job.panel_hours, 0.0,
            "Une séance seulement planifiée est comptée comme du temps consommé.",
        )
        held = self._make_interview([self.paid_user], duration=1.0)
        self.assertEqual(self.job.panel_hours, 1.0)

        held.action_cancel()
        self.job.invalidate_recordset()
        self.assertEqual(
            self.job.panel_hours, 0.0,
            "Une séance annulée continue de coûter du temps.",
        )

    # ------------------------------------------------------------------
    # 2. Le taux
    # ------------------------------------------------------------------

    def test_employee_rate_is_used(self):
        self._make_interview([self.paid_user], duration=2.0)
        self.assertEqual(self.job.panel_cost, 100.0)
        self.assertEqual(self.job.panel_hours_unpriced, 0.0)

    def test_fallback_covers_a_panelist_without_employee(self):
        """Le panel est fait de comptes, pas d'employés. Le repli existe pour ça."""
        self._make_interview([self.free_user], duration=2.0)
        self.assertEqual(
            self.job.panel_hours_unpriced, 2.0,
            "Sans repli, ces heures doivent être déclarées non valorisées.",
        )

        self.company.recruitment_panel_hourly_cost = 30.0
        self.job.invalidate_recordset()
        self.assertEqual(self.job.panel_cost, 60.0)
        self.assertEqual(self.job.panel_hours_unpriced, 0.0)

    def test_employee_rate_wins_over_the_fallback(self):
        """Le repli est un filet, pas une moyenne qui écrase les taux réels."""
        self.company.recruitment_panel_hourly_cost = 30.0
        self._make_interview([self.paid_user, self.free_user], duration=1.0)
        # 1 h à 50 (employé) + 1 h à 30 (repli).
        self.assertEqual(self.job.panel_cost, 80.0)

    def test_employee_of_another_company_lends_no_rate(self):
        """Un coût horaire appartient à l'employeur, pas à qui recrute."""
        self._make_interview([self.elsewhere_user], duration=2.0)
        self.assertEqual(
            self.job.panel_cost, 0.0,
            "Le taux d'une autre société a été emprunté par le poste.",
        )
        self.assertEqual(self.job.panel_hours_unpriced, 2.0)

    def test_a_zero_hourly_cost_falls_back(self):
        """Un employé sans taux posé n'est pas un employé à zéro dollar l'heure."""
        self.paid_employee.hourly_cost = 0.0
        self.company.recruitment_panel_hourly_cost = 25.0
        self._make_interview([self.paid_user], duration=2.0)
        self.assertEqual(self.job.panel_cost, 50.0)

    # ------------------------------------------------------------------
    # 3. Ce que le chiffre avoue
    # ------------------------------------------------------------------

    def test_unpriced_hours_are_named_not_silently_zeroed(self):
        """La propriété qui fait tout le module."""
        self._make_interview([self.paid_user, self.free_user], duration=2.0)
        self.assertEqual(self.job.panel_hours, 4.0)
        self.assertEqual(self.job.panel_hours_unpriced, 2.0)
        self.assertTrue(self.job.cost_is_partial)
        self.assertIn("2.00 h", self.job.cost_warning)
        self.assertIn("4.00", self.job.cost_warning)

    def test_the_warning_never_speaks_in_money(self):
        """`cost_warning` est visible au recrutement, les montants ne le sont pas."""
        self.company.recruitment_panel_hourly_cost = 0.0
        self._make_interview([self.free_user], duration=2.0)
        self._make_expense(500.0)
        warning = self.job.cost_warning
        self.assertTrue(warning)
        for leak in ("500", "$", str(self.job.sudo().panel_cost)):
            if leak in ("0.0",):
                continue
            self.assertNotIn(
                leak, warning,
                "L'avertissement laisse fuir un montant vers un lecteur qui "
                "n'a pas le droit de le voir.",
            )

    # ------------------------------------------------------------------
    # 4. Les débours
    # ------------------------------------------------------------------

    def test_expense_total_follows_the_job_and_excludes_refused(self):
        self._make_expense(300.0)
        self._make_expense(200.0)
        self._make_expense(999.0, refused=True)
        self.assertEqual(self.job.expense_count, 2)
        self.assertEqual(self.job.recruitment_expense_total, 500.0)

    def test_job_analytic_key_flows_down_to_the_expense(self):
        account = self.env["account.analytic.account"].create({
            "name": "Recrutement analyste",
            "plan_id": self.env["account.analytic.plan"].search([], limit=1).id,
            "company_id": self.company.id,
        })
        self.job.analytic_distribution = {str(account.id): 100}
        expense = self._make_expense(120.0)
        self.assertEqual(expense.analytic_distribution, {str(account.id): 100})

    def test_a_hand_written_distribution_is_never_overwritten(self):
        plan = self.env["account.analytic.plan"].search([], limit=1)
        job_account = self.env["account.analytic.account"].create({
            "name": "Poste", "plan_id": plan.id, "company_id": self.company.id,
        })
        hand_account = self.env["account.analytic.account"].create({
            "name": "À la main", "plan_id": plan.id, "company_id": self.company.id,
        })
        self.job.analytic_distribution = {str(job_account.id): 100}
        expense = self.env["hr.expense"].create({
            "name": "Annonce",
            "employee_id": self.spender.id,
            "product_id": self.expense_product.id,
            "company_id": self.company.id,
            "total_amount_currency": 100.0,
            "analytic_distribution": {str(hand_account.id): 100},
        })
        expense.job_id = self.job
        self.assertEqual(
            expense.analytic_distribution, {str(hand_account.id): 100},
            "Le poste a écrasé une distribution posée à la main.",
        )

    # ------------------------------------------------------------------
    # 5. Le coût par embauche
    # ------------------------------------------------------------------

    def test_no_hire_means_no_cost_per_hire(self):
        self._make_expense(400.0)
        self.assertEqual(self.job.no_of_hired_employee, 0)
        self.assertEqual(self.job.cost_per_hire, 0.0)
        self.assertTrue(self.job.cost_is_partial)
        self.assertIn("Aucune embauche", self.job.cost_warning)

    def test_cost_per_hire_divides_by_the_hires(self):
        self.company.recruitment_panel_hourly_cost = 0.0
        applicant = self._make_applicant("Personne embauchée")
        self._make_interview([self.paid_user], duration=2.0, applicant=applicant)
        self._make_expense(400.0)
        applicant.date_closed = "2026-03-20 10:00:00"
        self.job.invalidate_recordset()

        self.assertEqual(self.job.no_of_hired_employee, 1)
        # 400 de débours + 2 h à 50 = 500, pour une embauche.
        self.assertEqual(self.job.recruitment_cost_total, 500.0)
        self.assertEqual(self.job.cost_per_hire, 500.0)
        self.assertFalse(
            self.job.cost_is_partial,
            "Un chiffre complet ne doit pas se déclarer incomplet.",
        )

    # ------------------------------------------------------------------
    # 6. La donnée salariale reste où le coeur l'a mise
    # ------------------------------------------------------------------

    def test_salary_derived_fields_discriminate_between_two_readers(self):
        """La paire, pas l'absence : le recruteur est refusé, le RH est servi."""
        self.company.recruitment_panel_hourly_cost = 0.0
        self._make_interview([self.paid_user], duration=2.0)

        recruiter = self.env["res.users"].create({
            "name": "Recruteuse", "login": "recruteuse",
            "email": "recruteuse@example.invalid",
            "company_id": self.company.id,
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("hr_recruitment.group_hr_recruitment_user").id,
            ])],
        })
        hr_officer = self.env["res.users"].create({
            "name": "Gestionnaire RH", "login": "rh",
            "email": "rh@example.invalid",
            "company_id": self.company.id,
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("hr_recruitment.group_hr_recruitment_user").id,
                self.env.ref("hr.group_hr_user").id,
            ])],
        })

        job_as_recruiter = self.job.with_user(recruiter)
        # Les heures, oui : elles ne disent rien d'un salaire.
        self.assertEqual(job_as_recruiter.panel_hours, 2.0)
        for restricted in ("panel_cost", "recruitment_cost_total", "cost_per_hire"):
            with self.assertRaises(AccessError, msg=(
                "Le champ « %s » est lisible par un recruteur : le coût horaire "
                "d'un employé se déduit alors d'une durée connue." % restricted
            )):
                job_as_recruiter.read([restricted])

        job_as_hr = self.job.with_user(hr_officer)
        self.assertEqual(
            job_as_hr.panel_cost, 100.0,
            "Le gestionnaire RH est refusé lui aussi : la restriction ne "
            "discrimine pas, elle bloque tout le monde.",
        )
