# -*- coding: utf-8 -*-
from datetime import date, datetime

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import JobCase

PREP = "bf_job_search.mail_act_job_prepare"
THANKS = "bf_job_search.mail_act_job_thanks"
OFFER = "bf_job_search.mail_act_job_offer"


@tagged("post_install", "-at_install", "bf_job_search")
class TestInterviews(JobCase):

    @freeze_time("2031-03-03 12:00:00")
    def test_entrevue_prepare_et_remerciement_le_lendemain(self):
        app = self.app(self.env_anne, stage="applied")
        itv = self.env_anne["bf.job.interview"].create({
            "application_id": app.id, "date_start": datetime(2031, 3, 10, 14, 0),
            "interviewers": "Jane Doe <jane@example.com>\nSam Roe"})
        self.assertEqual(app.stage, "interview")
        self.assertEqual(itv.prep_activity_id.date_deadline, date(2031, 3, 9))
        self.assertEqual(itv.thanks_activity_id.date_deadline, date(2031, 3, 11))
        self.assertEqual(itv.thanks_activity_id.user_id, self.anne)
        # Un brouillon par intervieweur, au prénom.
        self.assertEqual(itv.thank_you_draft.count("Subject:"), 2)
        self.assertIn("Hello Jane,", itv.thank_you_draft)
        self.assertIn("Hello Sam,", itv.thank_you_draft)

    def test_brouillon_francais_sans_elision_manquante(self):
        # Un poste qui commence par une voyelle ne donne plus « le poste de Analyste ».
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_job_search"], ["fr_CA"], overwrite=True)
        app = self.app(self.env_anne, stage="applied", name="Analyste d'affaires")
        itv = self.env_anne["bf.job.interview"].create({
            "application_id": app.id, "interviewers": "Nadia Belkacem"})
        itv.invalidate_recordset(["thank_you_draft"])
        draft = itv.with_context(lang="fr_CA").thank_you_draft
        self.assertIn("Bonjour Nadia,", draft)
        self.assertIn("pour le poste « Analyste d'affaires »", draft)
        self.assertNotIn("de Analyste", draft)

    @freeze_time("2031-03-03 12:00:00")
    def test_date_inconnue_puis_confirmee(self):
        app = self.app(self.env_anne, stage="applied")
        itv = self.env_anne["bf.job.interview"].create({"application_id": app.id})
        self.assertEqual(itv.prep_activity_id.date_deadline, date(2031, 3, 3))
        self.assertFalse(itv.thanks_activity_id)
        itv.date_start = datetime(2031, 3, 12, 9, 0)
        self.assertEqual(itv.prep_activity_id.date_deadline, date(2031, 3, 11))
        self.assertEqual(itv.thanks_activity_id.date_deadline, date(2031, 3, 13))
        self.assertEqual(len(self.acts(app, PREP)), 1)

    @freeze_time("2031-03-03 12:00:00")
    def test_deux_entrevues_deux_jeux_d_activites(self):
        app = self.app(self.env_anne, stage="applied")
        a = self.env_anne["bf.job.interview"].create(
            {"application_id": app.id, "date_start": datetime(2031, 3, 5, 9, 0)})
        b = self.env_anne["bf.job.interview"].create(
            {"application_id": app.id, "date_start": datetime(2031, 3, 20, 9, 0), "kind": "panel"})
        self.assertEqual(len(self.acts(app, THANKS)), 2)
        a.action_thanked()
        self.assertEqual(a.thanked_date, date(2031, 3, 3))
        self.assertEqual(self.acts(app, THANKS), b.thanks_activity_id)

    @freeze_time("2031-03-03 12:00:00")
    def test_faire_l_activite_note_le_remerciement(self):
        app = self.app(self.env_anne, stage="applied")
        itv = self.env_anne["bf.job.interview"].create(
            {"application_id": app.id, "date_start": datetime(2031, 3, 2, 9, 0)})
        itv.thanks_activity_id.with_env(self.env_anne).action_feedback(feedback="fait")
        self.assertEqual(itv.thanked_date, date(2031, 3, 3))

    @freeze_time("2031-03-03 12:00:00")
    def test_preparee_retire_l_activite(self):
        app = self.app(self.env_anne, stage="applied")
        itv = self.env_anne["bf.job.interview"].create({"application_id": app.id})
        itv.action_prepared()
        self.assertEqual(itv.prep_progress, 100)
        self.assertFalse(self.acts(app, PREP))

    @freeze_time("2031-03-03 12:00:00")
    def test_agenda_prive_sans_invitation(self):
        app = self.app(self.env_anne, stage="applied", employer="Wellington Water")
        itv = self.env_anne["bf.job.interview"].create(
            {"application_id": app.id, "date_start": datetime(2031, 3, 10, 14, 0), "duration": 1.5})
        with self.assertRaises(UserError):
            self.env_anne["bf.job.interview"].create({"application_id": app.id}).action_add_to_calendar()
        mails_before = self.env["mail.mail"].sudo().search_count([])
        itv.action_add_to_calendar()
        ev = itv.calendar_event_id
        self.assertEqual(ev.privacy, "private")
        self.assertEqual(ev.partner_ids, self.anne.partner_id)
        self.assertEqual(ev.stop, datetime(2031, 3, 10, 15, 30))
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), mails_before)
        # Bruno voit un créneau occupé, pas l'employeur.
        # Odoo masque l'événement privé à la lecture en base, pas dans le cache de la
        # transaction : on lit comme l'écran de Bruno, cache vidé.
        self.env.invalidate_all()
        lu = ev.with_env(self.env_bruno).read(["name", "location"])[0]
        self.assertNotIn("Wellington", lu["name"] or "")
        # Déplacer l'entrevue déplace l'événement.
        itv.date_start = datetime(2031, 3, 11, 10, 0)
        self.assertEqual(ev.start, datetime(2031, 3, 11, 10, 0))

    @freeze_time("2031-03-03 12:00:00")
    def test_offre_et_sa_date_limite(self):
        app = self.app(self.env_anne, stage="interview", offer_deadline="2031-03-07")
        app.stage = "offer"
        self.assertEqual(app.offer_date, date(2031, 3, 3))
        act = self.acts(app, OFFER)
        self.assertEqual(act.date_deadline, date(2031, 3, 7))
        self.assertIn("writing", act.note)
        app.stage = "accepted"
        self.assertFalse(self.acts(app, OFFER))
        self.assertEqual(app.closed_by, "hired")
        self.assertTrue(app.is_closed)
