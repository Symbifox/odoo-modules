# -*- coding: utf-8 -*-
from datetime import date

from freezegun import freeze_time

from odoo.tests import tagged

from .common import JobCase

REFS = "bf_job_search.mail_act_job_referees"


@tagged("post_install", "-at_install", "bf_job_search")
class TestReferees(JobCase):

    @freeze_time("2031-03-03")
    def test_entrevue_demande_de_prevenir_les_repondants(self):
        carla = self.env_anne["bf.job.referee"].create({"name": "Carla Diaz", "consent_date": "2031-01-10"})
        app = self.app(self.env_anne, stage="applied")
        self.env_anne["bf.job.reference"].create({"application_id": app.id, "referee_id": carla.id})
        self.assertFalse(self.acts(app, REFS))
        app.stage = "interview"
        act = self.acts(app, REFS)
        self.assertEqual(len(act), 1)
        self.assertIn("Carla Diaz", act.note)
        app.action_warn_referees()
        self.assertFalse(self.acts(app, REFS))
        self.assertEqual(app.reference_ids.state, "warned")
        self.assertEqual(carla.last_briefed, date(2031, 3, 3))
        self.assertEqual(carla.times_named, 1)

    @freeze_time("2031-03-03")
    def test_repondant_nomme_pendant_l_entrevue(self):
        carla = self.env_anne["bf.job.referee"].create({"name": "Carla"})
        app = self.app(self.env_anne, stage="interview")
        self.assertFalse(self.acts(app, REFS))
        line = self.env_anne["bf.job.reference"].create({"application_id": app.id, "referee_id": carla.id})
        self.assertTrue(self.acts(app, REFS))
        line.action_called()
        self.assertEqual(line.state, "called")
        self.assertEqual(line.date_called, date(2031, 3, 3))

    def test_un_repondant_une_fois_par_candidature(self):
        carla = self.env_anne["bf.job.referee"].create({"name": "Carla"})
        app = self.app(self.env_anne)
        self.env_anne["bf.job.reference"].create({"application_id": app.id, "referee_id": carla.id})
        with self.assertRaises(Exception), self.cr.savepoint():
            self.env_anne["bf.job.reference"].create({"application_id": app.id, "referee_id": carla.id})
