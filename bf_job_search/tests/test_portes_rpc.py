# -*- coding: utf-8 -*-
"""Les portes RPC d'une fiche privée : onchange, Many2one réécrits, valeurs par défaut du
contexte, abonnés, suivi, rattachement chez autrui. Une par essai."""
from datetime import date, datetime

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import JobCase


@tagged("post_install", "-at_install", "bf_job_search")
class TestPortesRpc(JobCase):

    def test_1_onchange_ne_lit_pas_la_candidature_d_autrui(self):
        chez_anne = self.app(self.env_anne, name="Poste secret", employer="Employeur secret")
        Itv = self.env_bruno["bf.job.interview"]
        try:
            res = Itv.onchange({"application_id": chez_anne.id}, ["application_id"],
                               {"employer": {}, "position": {}, "name": {},
                                "user_id": {"fields": {"display_name": {}}}})
        except AccessError:
            return
        texte = str(res)
        self.assertNotIn("secret", texte)
        self.assertNotIn("Anne", texte)

    def test_2_piece_jointe_d_autrui_refusee(self):
        chez_anne = self.app(self.env_anne)
        cv = self.env["ir.attachment"].create({"name": "CV - Acme.pdf", "raw": b"x",
                                               "res_model": "bf.job.application", "res_id": chez_anne.id})
        sienne = self.app(self.env_bruno)
        with self.assertRaises(ValidationError):
            sienne.write({"cv_attachment_id": cv.id})
        # Sa propre pièce, jointe à SA candidature : acceptée.
        mien = self.env_bruno["ir.attachment"].create(
            {"name": "mon cv.pdf", "raw": b"y", "res_model": "bf.job.application", "res_id": sienne.id})
        sienne.write({"cv_attachment_id": mien.id})
        self.assertEqual(sienne.cv_attachment_id, mien)

    def test_2_lien_d_activite_ecrit_en_rpc_refuse(self):
        chez_anne = self.app(self.env_anne)
        act = chez_anne.activity_schedule("mail.mail_activity_data_todo", summary="Secret", user_id=self.anne.id)
        sienne = self.app(self.env_bruno, stage="applied")
        itv = self.env_bruno["bf.job.interview"].create({"application_id": sienne.id})
        for champ, valeur in (("prep_activity_id", act.id), ("thanks_activity_id", act.id),
                              ("calendar_event_id", 1)):
            with self.assertRaises(AccessError, msg=champ):
                itv.write({champ: valeur})
        with self.assertRaises(AccessError):
            self.env_bruno["bf.job.interview"].create(
                {"application_id": sienne.id, "thanks_activity_id": act.id})
        # Une clé de contexte posée par l'appelant RPC n'ouvre rien.
        for cle in ("bf_job_internal", "bf_job_from_button"):
            with self.assertRaises(AccessError, msg=cle):
                itv.with_context(**{cle: True}).write({"thanks_activity_id": act.id})

    def test_3_lignes_d_abonnes_illisibles(self):
        chez_anne = self.app(self.env_anne)
        self.assertTrue(chez_anne.sudo().message_follower_ids)
        lignes = self.env_bruno["mail.followers"].search_read(
            [("res_model", "=", "bf.job.application")], ["res_id", "partner_id"])
        self.assertFalse([l for l in lignes if l["res_id"] == chez_anne.id])
        # Anne voit la sienne.
        self.assertTrue(self.env_anne["mail.followers"].search(
            [("res_model", "=", "bf.job.application"), ("res_id", "=", chez_anne.id)]))

    def test_4_rien_ne_s_injecte_chez_autrui(self):
        sienne = self.app(self.env_bruno, stage="applied")
        with self.assertRaises(AccessError):
            sienne.write({"user_id": self.anne.id})
        ref = self.env_bruno["bf.job.referee"].create({"name": "Carla"})
        with self.assertRaises(AccessError):
            ref.write({"user_id": self.anne.id})
        crit = self.env_bruno["bf.job.criteria"]._for_user()
        with self.assertRaises(AccessError):
            crit.write({"user_id": self.anne.id})
        chez_anne = self.app(self.env_anne)
        itv = self.env_bruno["bf.job.interview"].create({"application_id": sienne.id})
        with self.assertRaises((ValidationError)):
            itv.write({"application_id": chez_anne.id})
        line = self.env_bruno["bf.job.reference"].create({"application_id": sienne.id, "referee_id": ref.id})
        with self.assertRaises(ValidationError):
            line.write({"application_id": chez_anne.id})
        # Candidature ET répondant d'Anne ensemble : la cohérence tient, la propriété non.
        ref_anne = self.env_anne["bf.job.referee"].create({"name": "Daniel"})
        with self.assertRaises(ValidationError):
            line.write({"application_id": chez_anne.id, "referee_id": ref_anne.id})
        self.assertFalse(self.env_anne["bf.job.interview"].search([]))

    def test_5_pas_de_suivi_lisible_par_un_admin(self):
        app = self.app(self.env_anne, name="Poste suivi", employer="Employeur suivi")
        self.env.flush_all()
        self.env.cr.precommit.run()
        app.write({"name": "Poste suivi 2", "employer": "Employeur suivi 2", "stage": "applied"})
        # Le suivi s'écrit au commit : on le déclenche.
        self.env.flush_all()
        self.env.cr.precommit.run()
        # L'administratrice cherche les valeurs par le champ suivi, sans passer par le
        # message (qu'elle ne peut pas lire) : c'est la porte.
        valeurs = self.env_admin["mail.tracking.value"].search_read(
            [("field_id.model", "=", "bf.job.application")], ["old_value_char", "new_value_char"])
        self.assertTrue(valeurs)  # l'étape, elle, reste suivie : l'essai mesure quelque chose
        self.assertFalse([v for v in valeurs if "suivi" in str(v)])

    @freeze_time("2031-03-03 12:00:00")
    def test_6_effacer_la_date_retire_l_evenement(self):
        app = self.app(self.env_anne, stage="applied")
        itv = self.env_anne["bf.job.interview"].create(
            {"application_id": app.id, "date_start": datetime(2031, 3, 10, 14, 0)})
        itv.action_add_to_calendar()
        ev = itv.calendar_event_id
        itv.date_start = False
        self.assertFalse(itv.calendar_event_id)
        self.assertFalse(ev.exists())

    @freeze_time("2031-03-03 12:00:00")
    def test_supprimer_l_entrevue_retire_l_evenement(self):
        app = self.app(self.env_anne, stage="applied")
        itv = self.env_anne["bf.job.interview"].create(
            {"application_id": app.id, "date_start": datetime(2031, 3, 10, 14, 0)})
        itv.action_add_to_calendar()
        ev = itv.calendar_event_id
        itv.unlink()
        self.assertFalse(ev.exists())

    def test_rouvrir_reprend_les_relances(self):
        with freeze_time("2031-03-03"):
            app = self.app(self.env_anne, stage="applied")
            self.env_anne["bf.job.close.wizard"].create(
                {"application_ids": [(6, 0, app.ids)], "outcome": "no_response"}).action_close()
            self.assertFalse(app.activity_ids)
            app.action_reopen()
            self.assertEqual(self.acts(app, "bf_job_search.mail_act_job_followup").date_deadline,
                             date(2031, 3, 13))

    def test_glisser_une_fiche_fermee_refuse(self):
        app = self.app(self.env_anne, stage="applied")
        self.env_anne["bf.job.close.wizard"].create(
            {"application_ids": [(6, 0, app.ids)], "outcome": "rejected", "ask_feedback": False}).action_close()
        with self.assertRaises(UserError):
            app.write({"stage": "accepted"})

    def test_purge_retire_les_fichiers_des_messages(self):
        with freeze_time("2030-01-10"):
            app = self.app(self.env_anne, stage="applied", date_applied="2030-01-02")
            msg = app.message_post(body="avec fichier")
            att = self.env["ir.attachment"].create({"name": "notes.pdf", "raw": b"z",
                                                    "res_model": "mail.message", "res_id": msg.id})
            msg.sudo().attachment_ids = [(4, att.id)]
            self.env_anne["bf.job.close.wizard"].create(
                {"application_ids": [(6, 0, app.ids)], "outcome": "no_response"}).action_close()
        with freeze_time("2031-06-01"):
            self.env_anne["bf.job.journal.wizard"].create({"purge_before": "2031-01-01"}).action_purge()
        self.assertFalse(att.exists())

    @freeze_time("2031-03-03 12:00:00")
    def test_nom_de_l_entrevue_au_jour_local(self):
        self.anne.tz = "Pacific/Auckland"
        app = self.app(self.env_anne, stage="applied")
        # 9 h le 11 mars à Auckland = 20 h UTC le 10 mars.
        itv = self.env_anne["bf.job.interview"].create(
            {"application_id": app.id, "date_start": datetime(2031, 3, 10, 20, 0)})
        self.assertIn("2031-03-11", itv.name)

    def test_tableau_borne(self):
        self.app(self.env_anne, stage="applied")
        s = self.env_anne["bf.job.stats"].create({"weeks": 10 ** 8})
        self.assertLessEqual(s.weeks_html.count("<tr>"), 105)

    # ------------------------------------- valeurs par défaut, lignes, copies
    def test_s1_default_du_contexte_ne_pose_pas_de_lien_interne(self):
        chez_anne = self.app(self.env_anne)
        act = chez_anne.activity_schedule("mail.mail_activity_data_todo", summary="Secret",
                                          user_id=self.anne.id)
        sienne = self.app(self.env_bruno, stage="applied")
        for champ in ("prep_activity_id", "thanks_activity_id"):
            with self.assertRaises(AccessError, msg=champ):
                self.env_bruno["bf.job.interview"].with_context(**{"default_" + champ: act.id}).create(
                    {"application_id": sienne.id})

    def test_s2_onchange_par_default_application_id(self):
        chez_anne = self.app(self.env_anne, name="Poste secret", employer="Employeur secret")
        Itv = self.env_bruno["bf.job.interview"].with_context(default_application_id=chez_anne.id)
        try:
            res = Itv.onchange({}, [], {"application_id": {}, "employer": {}, "position": {},
                                        "user_id": {"fields": {"display_name": {}}}})
        except AccessError:
            return
        self.assertNotIn("secret", str(res))
        self.assertNotIn("Anne", str(res))
        # Le flux normal, depuis sa propre candidature, passe.
        sienne = self.app(self.env_bruno, employer="Chez moi")
        res = self.env_bruno["bf.job.interview"].with_context(default_application_id=sienne.id).onchange(
            {}, [], {"application_id": {}, "employer": {}})
        self.assertIn("Chez moi", str(res))

    def test_s3_onchange_par_ligne_x2many(self):
        chez_anne = self.app(self.env_anne, name="Poste secret", employer="Employeur secret")
        try:
            res = self.env_bruno["bf.job.referee"].onchange(
                {"name": "Carla", "reference_ids": [[0, 0, {"application_id": chez_anne.id}]]},
                ["reference_ids"],
                {"reference_ids": {"fields": {"application_id": {"fields": {"display_name": {}}},
                                              "user_id": {"fields": {"display_name": {}}}}}})
        except AccessError:
            return
        self.assertNotIn("Anne", str(res))
        self.assertNotIn("secret", str(res))

    @freeze_time("2031-03-03 12:00:00")
    def test_dupliquer_ne_recopie_pas_les_liens(self):
        app = self.app(self.env_anne, stage="applied")
        cv = self.env_anne["ir.attachment"].create(
            {"name": "cv.pdf", "raw": b"x", "res_model": "bf.job.application", "res_id": app.id})
        app.cv_attachment_id = cv
        copie = app.copy()
        self.assertFalse(copie.cv_attachment_id)
        itv = self.env_anne["bf.job.interview"].create(
            {"application_id": app.id, "date_start": datetime(2031, 3, 10, 14, 0)})
        itv.action_add_to_calendar()
        double = itv.copy()
        self.assertFalse(double.calendar_event_id)
        self.assertNotEqual(double.thanks_activity_id, itv.thanks_activity_id)

    def test_etape_et_resultat_ensemble_refuses_sur_une_fermee(self):
        app = self.app(self.env_anne, stage="applied")
        self.env_anne["bf.job.close.wizard"].create(
            {"application_ids": [(6, 0, app.ids)], "outcome": "rejected", "ask_feedback": False}).action_close()
        with self.assertRaises(UserError):
            app.write({"stage": "accepted", "outcome": "withdrawn"})
        # Rouvrir et changer d'étape d'un coup reste permis.
        app.write({"stage": "interview", "outcome": False})
        self.assertEqual(app.stage, "interview")
