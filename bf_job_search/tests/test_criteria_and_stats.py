# -*- coding: utf-8 -*-
from freezegun import freeze_time

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import JobCase


@tagged("post_install", "-at_install", "bf_job_search")
class TestCriteriaAndStats(JobCase):

    def test_criteres_signalent_sans_jeter(self):
        crit = self.env_anne["bf.job.criteria"]._for_user()
        crit.write({"salary_floor": 80000, "min_hours_week": 30, "min_duration_months": 12,
                    "excluded_employers": "Initech\n", "excluded_words": "casino\nalcohol"})
        ok = self.app(self.env_anne, salary_max=95000, hours_per_week=40)
        self.assertFalse(ok.criteria_warning)
        bad = self.app(self.env_anne, employer="Initech Ltd", salary_max=70000, hours_per_week=20,
                       duration_months=6, posting="<p>Our Casino floor</p>")
        w = bad.criteria_warning
        for part in ("below my floor", "fewer hours", "shorter", "avoid", "casino"):
            self.assertIn(part, w)
        self.assertTrue(bad.exists())
        other = self.app(self.env_anne, salary_max=50, salary_period="hour")
        self.assertIn("not compared", other.criteria_warning)

    def test_criteres_de_chacun(self):
        a = self.env_anne["bf.job.criteria"]._for_user()
        self.assertEqual(self.env_anne["bf.job.criteria"]._for_user(), a)
        b = self.env_bruno["bf.job.criteria"]._for_user()
        self.assertNotEqual(a, b)
        with self.assertRaises(ValidationError):
            a.first_followup_days = 0

    @freeze_time("2031-03-12")
    def test_tableau_de_bord(self):
        Close = self.env_anne["bf.job.close.wizard"]
        a1 = self.app(self.env_anne, stage="applied", date_applied="2031-03-10", channel="referral")
        a2 = self.app(self.env_anne, stage="applied", date_applied="2031-03-02", channel="job_board")
        a3 = self.app(self.env_anne, stage="applied", date_applied="2031-03-01", channel="job_board")
        self.app(self.env_anne)  # repérée seulement : n'entre pas dans les taux
        a1.stage = "interview"                               # réponse au bout de 2 jours
        Close.create({"application_ids": [(6, 0, a2.ids)], "outcome": "rejected",
                      "ask_feedback": False}).action_close()  # réponse au bout de 10 jours
        Close.create({"application_ids": [(6, 0, a3.ids)], "outcome": "no_response"}).action_close()
        self.app(self.env_bruno, stage="applied", date_applied="2031-03-11")
        s = self.env_anne["bf.job.stats"].create({})
        self.assertEqual(s.applied_total, 3)
        self.assertEqual(s.this_week, 1)
        self.assertEqual(s.open_count, 2)
        self.assertEqual(round(s.response_rate), 67)
        self.assertEqual(round(s.interview_rate), 33)
        self.assertEqual(s.median_response_days, 6)
        self.assertIn("Referral", s.channels_html)
        self.assertIn("100 %", s.channels_html)
        self.assertIn("50 %", s.channels_html)

    def test_tableau_dans_la_langue_de_la_personne(self):
        if not self.env["res.lang"]._lang_get("fr_CA"):
            self.skipTest("fr_CA absent de la base d'essai")
        self.env["ir.module.module"].search([("name", "=", "bf_job_search")])._update_translations(["fr_CA"])
        self.anne.lang = "fr_CA"
        self.app(self.env_anne, stage="applied", channel="referral")
        s = self.env(user=self.anne, context={"lang": "fr_CA"})["bf.job.stats"].create({})
        self.assertIn("Recommandation", s.channels_html)
        self.assertNotIn("Referral", s.channels_html)
