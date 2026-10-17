# -*- coding: utf-8 -*-
from datetime import date, timedelta

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import JobCase

FOLLOW = "bf_job_search.mail_act_job_followup"
NO_NEWS = "bf_job_search.mail_act_job_no_news"


@tagged("post_install", "-at_install", "bf_job_search")
class TestFollowups(JobCase):

    @freeze_time("2031-03-03")
    def test_premiere_relance_a_dix_jours(self):
        app = self.app(self.env_anne)
        self.assertFalse(self.acts(app, FOLLOW))
        app.stage = "applied"
        self.assertEqual(app.date_applied, date(2031, 3, 3))
        act = self.acts(app, FOLLOW)
        self.assertEqual(len(act), 1)
        self.assertEqual(act.date_deadline, date(2031, 3, 13))
        self.assertEqual(act.user_id, self.anne)

    @freeze_time("2031-03-03")
    def test_date_promise_puis_lendemain(self):
        app = self.app(self.env_anne, stage="applied", reply_promised_date="2031-03-20")
        self.assertEqual(self.acts(app, FOLLOW).date_deadline, date(2031, 3, 21))
        # La promesse change : la relance suit.
        app.reply_promised_date = "2031-03-25"
        self.assertEqual(self.acts(app, FOLLOW).date_deadline, date(2031, 3, 26))

    @freeze_time("2031-03-03")
    def test_pas_de_relance_pendant_que_l_annonce_court(self):
        app = self.app(self.env_anne, stage="applied", closing_date="2031-03-30")
        self.assertEqual(self.acts(app, FOLLOW).date_deadline, date(2031, 3, 31))
        # Une annonce qui ferme avant les dix jours ne change rien.
        app2 = self.app(self.env_anne, stage="applied", closing_date="2031-03-05")
        self.assertEqual(self.acts(app2, FOLLOW).date_deadline, date(2031, 3, 13))

    def test_delais_reglables(self):
        crit = self.env_anne["bf.job.criteria"]._for_user()
        crit.write({"first_followup_days": 7, "next_followup_days": 5, "max_followups": 1})
        with freeze_time("2031-03-03"):
            app = self.app(self.env_anne, stage="applied")
            self.assertEqual(self.acts(app, FOLLOW).date_deadline, date(2031, 3, 10))
        with freeze_time("2031-03-10"):
            app.action_followup_sent()
            self.assertFalse(self.acts(app, FOLLOW))
            self.assertEqual(self.acts(app, NO_NEWS).date_deadline, date(2031, 3, 15))

    def test_deux_relances_puis_la_question_de_fermer(self):
        with freeze_time("2031-03-03"):
            app = self.app(self.env_anne, stage="applied")
        with freeze_time("2031-03-13"):
            app.action_followup_sent()
            self.assertEqual(app.followup_count, 1)
            self.assertEqual(app.last_followup_date, date(2031, 3, 13))
            self.assertEqual(self.acts(app, FOLLOW).date_deadline, date(2031, 3, 27))
        with freeze_time("2031-03-27"):
            app.action_followup_sent()
            self.assertEqual(app.followup_count, 2)
            self.assertFalse(self.acts(app, FOLLOW))
            self.assertEqual(self.acts(app, NO_NEWS).date_deadline, date(2031, 4, 10))
        # Le fil garde la trace, en notes internes.
        notes = app.message_ids.filtered(lambda m: "Follow-up" in (m.body or ""))
        self.assertEqual(len(notes), 2)

    def test_faire_l_activite_compte_la_relance(self):
        with freeze_time("2031-03-03"):
            app = self.app(self.env_anne, stage="applied")
        with freeze_time("2031-03-13"):
            self.acts(app, FOLLOW).with_env(self.env_anne).action_feedback(feedback="envoyée")
            self.assertEqual(app.followup_count, 1)
            self.assertEqual(self.acts(app, FOLLOW).date_deadline, date(2031, 3, 27))

    @freeze_time("2031-03-05")
    def test_reponse_arrete_les_relances(self):
        app = self.app(self.env_anne, stage="applied", date_applied="2031-03-01")
        app.action_response_received()
        self.assertFalse(self.acts(app, FOLLOW))
        self.assertEqual(app.date_first_response, date(2031, 3, 5))
        self.assertEqual(app.response_days, 4)

    @freeze_time("2031-03-05")
    def test_entrevue_arrete_les_relances(self):
        app = self.app(self.env_anne, stage="applied", date_applied="2031-03-01")
        app.stage = "interview"
        self.assertFalse(self.acts(app, FOLLOW))
        self.assertEqual(app.date_first_response, date(2031, 3, 5))

    def test_relance_refusee_hors_attente(self):
        app = self.app(self.env_anne)
        with self.assertRaises(UserError):
            app.action_followup_sent()
