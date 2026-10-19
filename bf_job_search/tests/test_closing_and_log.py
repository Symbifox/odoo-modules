# -*- coding: utf-8 -*-
import base64
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import JobCase

FEEDBACK = "bf_job_search.mail_act_job_feedback"


@tagged("post_install", "-at_install", "bf_job_search")
class TestClosingAndLog(JobCase):

    def _close(self, app, outcome, **kw):
        wiz = self.env_anne["bf.job.close.wizard"].create(
            dict({"application_ids": [(6, 0, app.ids)], "outcome": outcome}, **kw))
        wiz.action_close()

    def test_bandeau_ferme_au_singulier_en_francais(self):
        # Le bandeau d'une candidature fermée ne reprend plus le pluriel du filtre « Fermées ».
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_job_search"], ["fr_CA"], overwrite=True)
        V = self.env["ir.ui.view"].with_context(lang="fr_CA")
        form = V.browse(self.env.ref("bf_job_search.bf_job_application_view_form").id).arch_db
        search = V.browse(self.env.ref("bf_job_search.bf_job_application_view_search").id).arch_db
        self.assertIn('title="FERMÉE"', form)
        self.assertNotIn('title="Fermées"', form)
        self.assertIn('string="Fermées"', search)

    @freeze_time("2031-03-10")
    def test_qui_a_ferme(self):
        cases = {"rejected": "employer", "cancelled": "employer", "not_pursued": "me",
                 "withdrawn": "me", "declined": "me", "no_response": "nobody"}
        for outcome, who in cases.items():
            app = self.app(self.env_anne, stage="applied", date_applied="2031-03-01")
            self._close(app, outcome, ask_feedback=False)
            self.assertEqual(app.closed_by, who, outcome)
            self.assertTrue(app.is_closed)
            self.assertEqual(app.date_closed, date(2031, 3, 10))
            self.assertFalse(app.activity_ids, outcome)
            # Un refus est une réponse ; un silence n'en est pas une.
            self.assertEqual(bool(app.date_first_response), who == "employer", outcome)

    @freeze_time("2031-03-10")
    def test_refus_propose_de_demander_une_retroaction(self):
        app = self.app(self.env_anne, stage="applied")
        self._close(app, "rejected", ask_feedback=True)
        self.assertEqual(self.acts(app, FEEDBACK).date_deadline, date(2031, 3, 11))

    @freeze_time("2031-03-10")
    def test_fermee_ne_se_relance_pas_et_se_rouvre(self):
        app = self.app(self.env_anne, stage="applied")
        self._close(app, "no_response")
        with self.assertRaises(UserError):
            app.action_followup_sent()
        with self.assertRaises(UserError):
            self._close(app, "rejected")
        app.action_reopen()
        self.assertFalse(app.is_closed)
        self.assertEqual(app.closed_by, "open")

    @freeze_time("2031-06-01")
    def test_journal_csv(self):
        a = self.app(self.env_anne, stage="applied", date_applied="2031-04-02",
                     employer="Société Équipe", channel="referral")
        self._close(a, "rejected", ask_feedback=False)
        self.app(self.env_anne, stage="applied", date_applied="2031-05-20", employer="Beta")
        self.app(self.env_anne, stage="applied", date_applied="2030-01-01", employer="Trop vieux")
        self.app(self.env_bruno, stage="applied", date_applied="2031-05-01", employer="Chez Bruno")
        wiz = self.env_anne["bf.job.journal.wizard"].create({})
        wiz.action_export()
        text = base64.b64decode(wiz.file_data).decode("utf-8")
        self.assertTrue(text.startswith("﻿"))
        self.assertEqual(wiz.line_count, 2)
        self.assertIn("Société Équipe", text)
        self.assertIn("Rejected by the employer", text)
        self.assertIn("Referral", text)
        self.assertNotIn("Trop vieux", text)
        self.assertNotIn("Chez Bruno", text)

    def test_purge_garde_le_journal(self):
        with freeze_time("2030-01-10"):
            app = self.app(self.env_anne, stage="applied", date_applied="2030-01-02",
                           posting="<p>Annonce</p>", salary_max=90000, fit_note="note")
            self.env["ir.attachment"].create({"name": "cv.pdf", "raw": b"x",
                                              "res_model": "bf.job.application", "res_id": app.id})
            self.env_anne["bf.job.interview"].create({"application_id": app.id, "note": "secret"})
            app.message_post(body="détail")
            self._close(app, "rejected", ask_feedback=False)
            recent = self.app(self.env_anne, stage="applied", fit_note="garde")
        with freeze_time("2031-06-01"):
            wiz = self.env_anne["bf.job.journal.wizard"].create({"purge_before": "2031-01-01"})
            wiz.action_purge()
        self.env.invalidate_all()
        self.assertTrue(app.details_purged)
        self.assertFalse(app.posting)
        self.assertFalse(app.salary_max)
        self.assertFalse(app.interview_ids)
        self.assertFalse(self.env["ir.attachment"].search(
            [("res_model", "=", "bf.job.application"), ("res_id", "=", app.id)]))
        self.assertFalse(app.message_ids.filtered(lambda m: "détail" in (m.body or "")))
        # L'entrée du journal reste.
        self.assertEqual((app.employer, app.name, app.date_applied, app.outcome),
                         ("Acme Ltd", "Systems Analyst", date(2030, 1, 2), "rejected"))
        self.assertEqual(recent.fit_note, "garde")

    @freeze_time("2031-06-01")
    def test_purge_refuse_le_recent(self):
        wiz = self.env_anne["bf.job.journal.wizard"].create({"purge_before": "2031-05-20"})
        with self.assertRaises(UserError):
            wiz.action_purge()
