"""Mesures de vie privée portées par la 2.5.0 : rappels au nom de la personne, notes internes seulement, traces GPX non stockées.

Rappels : les rappels des crons sont posés au nom de la personne, leur note réduite
au nom de la fiche. Fil privé : le fil d'une fiche santé ne porte que des notes
internes et ne notifie personne. GPX : un GPX donne ses valeurs calculées, le
fichier n'est gardé nulle part. Données inventées.
"""
import base64
import re
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase
from odoo.tools.misc import file_path

from ..models.health_note_only import MODELES_NOTE_SEULE
from ..models.parent_guard import au_nom_du_proprietaire, fermee_a_cet_appel

GROUPES = "base.group_user,bf_health.group_health_user"
SECRET = "SECRET-SANTE-MESURES"

GPX = b"""<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
<trk><trkseg>
<trkpt lat="45.50" lon="-73.56"><ele>20</ele><time>2026-10-01T10:00:00Z</time></trkpt>
<trkpt lat="45.51" lon="-73.56"><ele>35</ele><time>2026-10-01T10:06:00Z</time></trkpt>
<trkpt lat="45.52" lon="-73.57"><ele>30</ele><time>2026-10-01T10:13:00Z</time></trkpt>
</trkseg></trk></gpx>"""


@tagged("post_install", "-at_install", "mesures_efvp")
class TestMesuresEfvp(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = new_test_user(cls.env, login="efvp_a", groups=GROUPES, name="Personne A",
                              email="efvp_a@banc.test", notification_type="email")
        cls.b = new_test_user(cls.env, login="efvp_b", groups=GROUPES, name="Personne B",
                              email="efvp_b@banc.test", notification_type="email")
        cls.dehors = cls.env["res.partner"].create({"name": "Abonné externe",
                                                    "email": "dehors@banc.test"})

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _med_a(self):
        return self._en(self.a, "health.medication").create({
            "name": "Médicament fictif", "dosage": "10 mg", "pharmacy": "Pharmacie fictive",
            "state": "active", "frequency": "daily",
            "renewal_date": fields.Date.context_today(self.env["health.medication"])})

    def _sorties(self, message):
        """Ce qui sortirait de l'instance pour ce message : courriels en file
        et notifications (boîte de réception ou courriel)."""
        mails = self.env["mail.mail"].sudo().search([("mail_message_id", "=", message.id)])
        notifs = self.env["mail.notification"].sudo().search([("mail_message_id", "=", message.id)])
        return mails, notifs

    # ------------------------------------------------------------------
    # Rappels au nom de la personne, note réduite au nom
    # ------------------------------------------------------------------
    def test_m5_notes_reduites_au_nom(self):
        aujourd_hui = fields.Date.context_today(self.env["health.medication"])
        med = self._med_a()
        lab = self._en(self.a, "health.lab.test").create({
            "name": "Analyse fictive", "ordering_doctor": "Dre Fictive",
            "lab_name": "Labo fictif", "next_due_date": aujourd_hui})
        scr = self._en(self.a, "health.screening").create({
            "name": "Examen fictif", "provider": "Clinique fictive", "frequency": "yearly",
            "last_date": aujourd_hui - timedelta(days=355)})
        self.env.invalidate_all()
        self.env["health.medication"]._cron_check_renewals()
        self.env["health.lab.test"]._cron_check_lab_tests()
        self.env["health.screening"]._cron_check_screenings()
        for rec, detail in ((med, ("10 mg", "Pharmacie fictive")),
                            (lab, ("Dre Fictive", "Labo fictif")),
                            (scr, ("Clinique fictive",))):
            with self.subTest(model=rec._name):
                act = self.env["mail.activity"].sudo().search([
                    ("res_model", "=", rec._name), ("res_id", "=", rec.id)])
                self.assertEqual(len(act), 1)
                self.assertEqual(act.user_id, self.a)
                self.assertEqual(act.create_uid, self.a)
                self.assertIn(rec.name, str(act.note))
                for mot in detail:
                    self.assertNotIn(mot, str(act.note))
                # Résumé neutre : il part dans les résumés quotidiens par courriel.
                self.assertTrue(act.summary.startswith("Healthy Fox : "), act.summary)
                self.assertNotIn(rec.name, act.summary)

    def test_m5_au_nom_de_la_personne_sans_dependre_du_canal(self):
        """Lancé depuis l'écran par une autre personne, le cron agit sous l'uid
        de la propriétaire, en superutilisateur : le verrou de Gen ne le prend
        plus pour le canal API."""
        med = self._med_a()
        fiche, responsable = au_nom_du_proprietaire(med.with_user(self.b))
        self.assertEqual(responsable, self.a.id)
        self.assertEqual(fiche.env.uid, self.a.id)
        self.assertTrue(fiche.env.su)

    # ------------------------------------------------------------------
    # Notes internes seulement, aucune notification
    # ------------------------------------------------------------------
    def test_m12_le_filtre_passe_avant_le_coeur(self):
        """Le mixin doit précéder `mail.thread` : sinon le `message_post` du
        cœur passe le premier et le filtre ne joue jamais."""
        for model in MODELES_NOTE_SEULE:
            with self.subTest(model=model):
                classes = type(self.env[model]).__mro__
                premier = next(c for c in classes if "message_post" in vars(c))
                self.assertTrue(premier.__module__.endswith("health_note_only"), premier)
                premier = next(c for c in classes if "_notify_get_recipients" in vars(c))
                self.assertTrue(premier.__module__.endswith("health_note_only"), premier)

    def test_m12_tous_les_fils_de_sante_et_l_ecran(self):
        """Toute fiche santé à fil est couverte, et l'écran porte la même liste."""
        a_fil = sorted(nom for nom, modele in self.env.registry.items()
                       if nom.startswith("health.") and not modele._abstract
                       and not modele._transient and "message_ids" in modele._fields)
        self.assertEqual(a_fil, sorted(MODELES_NOTE_SEULE))
        chemin = file_path("bf_health/static/src/xml/chatter_note_only.xml")
        with open(chemin, encoding="utf-8") as fichier:
            ecran = re.findall(r"'(health\.[a-z_.]+)'", fichier.read())
        self.assertEqual(sorted(ecran), sorted(MODELES_NOTE_SEULE))

    def test_m12_envoyer_un_message_devient_une_note(self):
        med = self._med_a()
        msg = med.with_user(self.a).message_post(
            body=SECRET, message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=[self.b.partner_id.id, self.dehors.id])
        self.assertEqual(msg.subtype_id, self.env.ref("mail.mt_note"))
        self.assertFalse(msg.sudo().partner_ids, "personne n'est ajouté en destinataire")
        mails, notifs = self._sorties(msg)
        self.assertFalse(mails)
        self.assertFalse(notifs)
        # B, mentionnée, ne lit pas le message (sa lecture passerait par partner_ids).
        self.assertFalse(self._en(self.b, "mail.message").search([("id", "=", msg.id)]))

    def test_m12_abonne_ajoute_jamais_notifie(self):
        med = self._med_a()
        types = [self.env.ref(x).id for x in ("mail.mt_comment", "mail.mt_note", "mail.mt_activities")]
        med.with_user(self.a).message_subscribe(partner_ids=[self.dehors.id, self.b.partner_id.id],
                                                subtype_ids=types)
        msg = med.with_user(self.a).message_post(
            body=SECRET, message_type="comment", subtype_xmlid="mail.mt_comment")
        mails, notifs = self._sorties(msg)
        self.assertFalse(mails)
        self.assertFalse(notifs)
        # Une activité terminée (sous-type « Activités ») ne sort pas non plus.
        act = med.with_user(self.a).activity_schedule(
            "mail.mail_activity_data_todo", summary="Rappel fictif", user_id=self.a.id)
        act.with_user(self.a).action_feedback(feedback=SECRET)
        fait = med.message_ids.filtered(lambda m: m.subtype_id.id == types[2])[:1]
        self.assertTrue(fait)
        mails, notifs = self._sorties(fait)
        self.assertFalse(mails)
        self.assertFalse(notifs)

    def test_m12_compositeur_envoyer_un_message(self):
        med = self._med_a()
        composer = self.env["mail.compose.message"].with_user(self.a).with_context(
            default_model="health.medication", default_res_ids=med.ids,
            default_composition_mode="comment").create({
                "body": SECRET, "partner_ids": [(6, 0, [self.dehors.id])],
                "subtype_id": self.env.ref("mail.mt_comment").id})
        composer._action_send_mail()
        msg = med.message_ids.filtered(lambda m: SECRET in str(m.body))
        self.assertEqual(len(msg), 1)
        self.assertEqual(msg.subtype_id, self.env.ref("mail.mt_note"))
        mails, notifs = self._sorties(msg)
        self.assertFalse(mails)
        self.assertFalse(notifs)
        self.assertFalse(self.env["mail.mail"].sudo().search([("body_html", "ilike", SECRET)]))

    def test_m12_avis_direct_et_abonnes_coupes(self):
        """L'invitation à suivre avec message passait par `message_notify` : la
        personne invitée lisait le nom et le texte de la fiche."""
        med = self._med_a()
        invite = self.env["mail.wizard.invite"].with_user(self.a).create({
            "res_model": "health.medication", "res_id": med.id, "notify": True,
            "message": SECRET, "partner_ids": [(6, 0, [self.b.partner_id.id, self.dehors.id])]})
        invite.add_followers()
        self.assertFalse(self.env["mail.message"].sudo().search([("body", "ilike", SECRET)]))
        self.assertNotIn(self.b.partner_id, med.sudo().message_partner_ids)
        self.assertNotIn(self.dehors, med.sudo().message_partner_ids)
        self.assertFalse(med.with_user(self.a).message_notify(
            body=SECRET, partner_ids=[self.b.partner_id.id]))

    def test_m12_activite_assignee_a_autrui_refusee(self):
        med = self._med_a()
        with self.assertRaises(ValidationError):
            med.with_user(self.a).activity_schedule(
                "mail.mail_activity_data_todo", summary="Rappel fictif", note=SECRET,
                user_id=self.b.id)
        act = med.with_user(self.a).activity_schedule(
            "mail.mail_activity_data_todo", summary="Rappel fictif", user_id=self.a.id)
        with self.assertRaises(ValidationError):
            act.with_user(self.a).write({"user_id": self.b.id})

    def test_m12_numeros_sms_retires(self):
        if "sms.sms" not in self.env:
            self.skipTest("module sms absent")
        med = self._med_a()
        med.with_user(self.a).message_post(body=SECRET, message_type="comment",
                                           sms_numbers=["+15145550101"])
        self.assertFalse(self.env["sms.sms"].sudo().search([("body", "ilike", SECRET)]))

    def test_m12_compositeur_en_masse_refuse(self):
        med = self._med_a()
        composer = self.env["mail.compose.message"].with_user(self.a).with_context(
            default_model="health.medication", default_res_ids=med.ids,
            default_composition_mode="mass_mail").create({
                "subject": SECRET, "body": SECRET,
                "partner_ids": [(6, 0, [self.dehors.id])]})
        with self.assertRaises(UserError):
            composer._action_send_mail()
        self.assertFalse(self.env["mail.mail"].sudo().search([("subject", "ilike", SECRET)]))

    # ------------------------------------------------------------------
    # Tableau de bord : fermé dès qu'une table est bornée par le verrou
    # ------------------------------------------------------------------
    def test_tableau_ferme_si_une_table_est_bornee_a_une_fiche(self):
        Regle = type(self.env["ir.rule"])
        original = Regle._compute_domain

        def borne(regle, model_name, mode="read"):
            if model_name == "health.vital":
                return [("id", "in", [1])]
            return original(regle, model_name, mode)

        env_a = self.env(user=self.a)
        self.assertFalse(fermee_a_cet_appel(env_a, ["health.vital", "health.medication"]))
        with patch.object(Regle, "_compute_domain", borne):
            self.assertTrue(fermee_a_cet_appel(env_a, ["health.vital", "health.medication"]))
            with self.assertRaises(Exception):
                env_a["health.dashboard"].get_dashboard_data()

    # ------------------------------------------------------------------
    # GPX, valeurs calculées seulement
    # ------------------------------------------------------------------
    def _aucune_trace(self, seance):
        self.env.flush_all()
        self.assertFalse(self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "health.workout"), ("res_id", "=", seance.id),
            "|", ("res_field", "=", False), ("res_field", "!=", False)]))
        self.env.cr.execute("""SELECT column_name FROM information_schema.columns
                               WHERE table_name = 'health_workout'
                                 AND column_name IN ('gpx_file', 'gpx_filename')""")
        for (colonne,) in self.env.cr.fetchall():
            self.env.cr.execute(f"SELECT {colonne} FROM health_workout WHERE id = %s", (seance.id,))
            self.assertIsNone(self.env.cr.fetchone()[0], colonne)
        self.env.invalidate_all()
        self.assertFalse(seance.gpx_file)
        self.assertFalse(seance.gpx_filename)

    def test_m13_creation_garde_les_valeurs_jette_le_fichier(self):
        seance = self._en(self.a, "health.workout").create({
            "name": "Course fictive", "gpx_file": base64.b64encode(GPX),
            "gpx_filename": "maison.gpx"})
        self.assertAlmostEqual(seance.distance_km, 2.47, places=2)
        self.assertEqual(seance.elevation_m, 15.0)
        self.assertEqual(seance.duration_min, 13.0)
        self._aucune_trace(seance)

    def test_m13_ecriture_sur_une_seance_existante(self):
        seance = self._en(self.a, "health.workout").create({"name": "Course fictive"})
        self.assertFalse(seance.distance_km)
        seance.write({"gpx_file": base64.b64encode(GPX), "gpx_filename": "chalet.gpx"})
        self.assertAlmostEqual(seance.distance_km, 2.47, places=2)
        self._aucune_trace(seance)

    def test_m13_fichier_illisible(self):
        with self.assertRaises(UserError):
            self._en(self.a, "health.workout").create({
                "name": "Course illisible", "gpx_file": base64.b64encode(b"<pas du gpx")})
