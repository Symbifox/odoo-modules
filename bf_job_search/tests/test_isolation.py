# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import JobCase


@tagged("post_install", "-at_install", "bf_job_search")
class TestIsolation(JobCase):

    def test_anne_ne_voit_ni_ne_touche_les_candidatures_de_bruno(self):
        chez_bruno = self.app(self.env_bruno, salary_max=95000)
        mienne = self.app(self.env_anne)
        self.assertTrue(chez_bruno.sudo().exists())
        Apps = self.env_anne["bf.job.application"].with_context(active_test=False)
        self.assertEqual(Apps.search([]), mienne)
        self.assertEqual(Apps.search_count([("id", "=", chez_bruno.id)]), 0)
        lu = chez_bruno.with_env(self.env_anne)
        with self.assertRaises(AccessError):
            lu.read(["salary_max"])
        with self.assertRaises(AccessError):
            lu.write({"name": "lu"})
        with self.assertRaises(AccessError):
            lu.unlink()
        self.assertEqual(chez_bruno.sudo().name, "Systems Analyst")

    def test_l_administratrice_non_plus(self):
        app = self.app(self.env_anne)
        self.assertFalse(self.env_admin["bf.job.application"].search([]))
        # Les étapes s'affichent toutes (group_expand), aucune ne compte la fiche d'Anne.
        groups = self.env_admin["bf.job.application"].read_group([], ["stage"], ["stage"])
        self.assertTrue(groups)
        self.assertFalse([g for g in groups if g["stage_count"]])
        with self.assertRaises(AccessError):
            app.with_env(self.env_admin).read(["name"])

    def test_creer_pour_autrui_refuse(self):
        with self.assertRaises(AccessError):
            self.env_anne["bf.job.application"].create(
                {"name": "x", "employer": "y", "user_id": self.bruno.id})

    def test_toutes_les_fiches_sont_privees(self):
        app = self.app(self.env_bruno, stage="applied")
        itv = self.env_bruno["bf.job.interview"].create({"application_id": app.id})
        ref_b = self.env_bruno["bf.job.referee"].create({"name": "Carla"})
        line = self.env_bruno["bf.job.reference"].create(
            {"application_id": app.id, "referee_id": ref_b.id})
        crit = self.env_bruno["bf.job.criteria"]._for_user()
        for rec in (itv, ref_b, line, crit):
            self.assertFalse(self.env_anne[rec._name].search([]), rec._name)
            with self.assertRaises(AccessError, msg=rec._name):
                rec.with_env(self.env_anne).read(["display_name"])

    def test_nommer_le_repondant_d_autrui_refuse(self):
        app = self.app(self.env_anne)
        carla = self.env_bruno["bf.job.referee"].create({"name": "Carla"})
        with self.assertRaises(ValidationError):
            self.env_anne["bf.job.reference"].create(
                {"application_id": app.id, "referee_id": carla.id})

    def test_le_nom_reste_prive_hors_lecture(self):
        app = self.app(self.env_bruno, name="Poste secret")
        # Lu en sudo par quelqu'un d'autre (Many2one, message de debug) : nom neutre.
        nom = app.with_env(self.env_anne).sudo().display_name
        self.assertNotIn("secret", nom)
        self.assertEqual(app.with_env(self.env_bruno).display_name, "Poste secret")

    def test_fil_notes_internes_sans_destinataire(self):
        app = self.app(self.env_anne)
        msg = app.message_post(body="Mon salaire actuel", subtype_xmlid="mail.mt_comment",
                               partner_ids=[self.bruno.partner_id.id])
        self.assertEqual(msg.subtype_id, self.env.ref("mail.mt_note"))
        self.assertFalse(msg.sudo().partner_ids)
        self.assertFalse(self.env["mail.notification"].sudo().search([("mail_message_id", "=", msg.id)]))
        # Un abonné qui serait là quand même (ligne posée en superutilisateur) ne
        # reçoit rien : le fil ne calcule aucun destinataire.
        self.env["mail.followers"].sudo().create({
            "res_model": "bf.job.application", "res_id": app.id,
            "partner_id": self.bruno.partner_id.id,
            "subtype_ids": [(6, 0, [self.env.ref("mail.mt_note").id])]})
        app.invalidate_recordset()
        msg2 = app.message_post(body="Deuxième note", subtype_xmlid="mail.mt_note")
        # Chez BF les notifications partent en différé (mail_post_defer) : on
        # interroge le calcul des destinataires lui-même. Le cœur y mettrait Bruno.
        self.assertEqual(app._notify_get_recipients(msg2, {
            "subtype_id": self.env.ref("mail.mt_note").id, "author_id": self.anne.partner_id.id,
            "message_type": "notification", "model": app._name, "res_id": app.id}), [])
        self.env["mail.followers"].sudo().search([
            ("res_model", "=", "bf.job.application"), ("res_id", "=", app.id),
            ("partner_id", "=", self.bruno.partner_id.id)]).unlink()
        # Abonner Bruno : refusé en silence (seule Anne suit sa fiche).
        app.message_subscribe(partner_ids=[self.bruno.partner_id.id])
        self.assertNotIn(self.bruno.partner_id, app.sudo().message_partner_ids)
        self.assertIn(self.anne.partner_id, app.sudo().message_partner_ids)

    def test_abonne_direct_refuse(self):
        app = self.app(self.env_anne)
        # Le cœur refuse déjà la création aux personnes ordinaires ; l'administratrice,
        # elle, en a le droit : c'est elle que la garde arrête.
        with self.assertRaises(AccessError):
            self.env_admin["mail.followers"].create({
                "res_model": "bf.job.application", "res_id": app.id,
                "partner_id": self.admin.partner_id.id})
        with self.assertRaises(AccessError):
            self.env_bruno["mail.followers"].create({
                "res_model": "bf.job.application", "res_id": app.id,
                "partner_id": self.bruno.partner_id.id})
        with self.assertRaises(AccessError):
            self.env_anne["mail.followers"].create({
                "res_model": "bf.job.application", "res_id": app.id,
                "partner_id": self.bruno.partner_id.id})

    def test_activite_ni_chez_autrui_ni_pour_autrui(self):
        app = self.app(self.env_anne)
        model_id = self.env["ir.model"]._get_id("bf.job.application")
        with self.assertRaises(AccessError):
            self.env_bruno["mail.activity"].create(
                {"res_model_id": model_id, "res_id": app.id, "summary": "x",
                 "user_id": self.bruno.id})
        with self.assertRaises(ValidationError):
            app.activity_schedule("mail.mail_activity_data_todo", summary="x", user_id=self.bruno.id)
        # Déplacer une activité de Bruno sur la fiche d'Anne : refusé.
        sienne = self.app(self.env_bruno)
        act = sienne.activity_schedule("mail.mail_activity_data_todo", summary="y",
                                       user_id=self.bruno.id)
        with self.assertRaises(AccessError):
            act.with_env(self.env_bruno).write({"res_id": app.id})

    def test_piece_jointe_deplacee_refusee(self):
        app = self.app(self.env_anne)
        sienne = self.app(self.env_bruno)
        # Le cœur contrôle la cible quand res_model ET res_id s'écrivent ensemble ; la
        # porte est l'écriture de res_id seul, depuis une pièce déjà sur SA fiche.
        att = self.env_bruno["ir.attachment"].create(
            {"name": "cv.pdf", "raw": b"x", "res_model": "bf.job.application", "res_id": sienne.id})
        with self.assertRaises(AccessError):
            att.write({"res_id": app.id})
        self.assertEqual(att.sudo().res_id, sienne.id)

    def test_tableau_et_assistants_prives(self):
        self.app(self.env_anne, stage="applied")
        stats = self.env_anne["bf.job.stats"].create({})
        self.assertEqual(stats.applied_total, 1)
        with self.assertRaises(AccessError):
            stats.with_env(self.env_bruno).read(["applied_total"])
        journal = self.env_anne["bf.job.journal.wizard"].create({})
        with self.assertRaises(AccessError):
            journal.with_env(self.env_bruno).read(["file_data"])

    def test_bruno_ne_ferme_pas_chez_anne(self):
        app = self.app(self.env_anne, stage="applied")
        wiz = self.env_bruno["bf.job.close.wizard"].create(
            {"application_ids": [(6, 0, [app.id])], "outcome": "rejected"})
        # La fiche d'Anne est invisible à Bruno : l'assistant n'a rien à fermer, et le dit.
        with self.assertRaises(UserError), mute_logger("odoo.models"):
            wiz.action_close()
        self.assertFalse(app.sudo().outcome)
        with self.assertRaises(AccessError):
            app.with_env(self.env_bruno).action_followup_sent()

    def test_envoi_de_masse_refuse(self):
        app = self.app(self.env_anne)
        wiz = self.env_anne["mail.compose.message"].with_context(
            default_model="bf.job.application", default_res_ids=[app.id],
            default_composition_mode="mass_mail").create(
            {"body": "x", "subject": "y", "composition_mode": "mass_mail"})
        with self.assertRaises(UserError):
            wiz._action_send_mail()
