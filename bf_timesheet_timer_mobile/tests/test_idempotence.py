"""Les gestes du chrono rejoués par la file hors ligne.

Trois garanties :

* un geste rejoué avec le même ``client_uuid`` rend sa première réponse, sans
  refaire le geste (un second chrono, une seconde feuille de temps) ;
* une pause mise en file AVANT que le démarrage soit monté vise le chrono par
  ``timer_uuid``, le ``client_uuid`` du démarrage ;
* ``at``, l'heure réelle du geste sur le téléphone, est bornée.
"""
import base64
import hashlib
import json
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

API = "/bf_timer/mobile/v1"
GROUPES = "base.group_user,hr_timesheet.group_hr_timesheet_user,project.group_project_user"


def _defi(verificateur):
    condense = hashlib.sha256(verificateur.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(condense).decode().rstrip("=")


def _ms(moment):
    """Un datetime naïf UTC en millisecondes epoch."""
    return int((moment - fields.Datetime.from_string("1970-01-01 00:00:00")).total_seconds() * 1000)


@tagged("post_install", "-at_install", "bf_timesheet_timer_mobile")
class TestRejeuChrono(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_timer.rounding_mode", "none")

        def personne(login):
            usager = new_test_user(cls.env, login=login, groups=GROUPES)
            cls.env["hr.employee"].create({"name": login, "user_id": usager.id})
            return usager

        cls.personne = personne("chrono_rejeu_t")
        cls.voisin = personne("chrono_rejeu_voisin_t")
        cls.projet = cls.env["project.project"].create({
            "name": "Projet rejeu", "allow_timesheets": True,
            "privacy_visibility": "employees"})
        cls.tache = cls.env["project.task"].create(
            {"name": "Tâche rejouée", "project_id": cls.projet.id})
        cls.autre_tache = cls.env["project.task"].create(
            {"name": "Autre tâche rejouée", "project_id": cls.projet.id})

    def _jeton(self, usager=None):
        Device = self.env["bf.timer.device"]
        verificateur = "verificateur-rejeu-%s" % uuid.uuid4()
        code = Device._issue_pending((usager or self.personne).id,
                                     challenge=_defi(verificateur))
        return Device._exchange(code, verificateur)[1]

    def setUp(self):
        super().setUp()
        self.jeton = self._jeton()

    def _post(self, chemin, charge, jeton=None):
        return self.url_open(API + chemin, data=json.dumps(charge), headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % (jeton or self.jeton)})

    def _chronos(self, usager=None):
        return self.env["bf.timer"].sudo().search(
            [("user_id", "=", (usager or self.personne).id)])

    def _demarrer(self, cle=None, tache=None, **extra):
        charge = {"task_id": (tache or self.tache).id, **extra}
        if cle is not None:
            charge["client_uuid"] = cle
        return self._post("/chrono/demarrer", charge)

    # ── Démarrer ──────────────────────────────────────────────────────

    def test_demarrage_rejoue_ne_cree_qu_un_chrono(self):
        cle = str(uuid.uuid4())
        a = self._demarrer(cle)
        b = self._demarrer(cle)
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.text)
        self.assertEqual(len(self._chronos()), 1)
        self.assertEqual(b.json(), dict(a.json(), replay=True))
        self.assertEqual(self._chronos().client_uuid, cle)

    def test_demarrage_connu_sans_accuse_rend_le_chrono_existant(self):
        """L'accusé purgé, le chrono garde son identifiant : toujours un seul."""
        cle = str(uuid.uuid4())
        a = self._demarrer(cle)
        self.env["bf.timer.mobile.receipt"].sudo().search([]).unlink()
        b = self._demarrer(cle)
        self.assertEqual(b.status_code, 200, b.text)
        self.assertTrue(b.json()["replay"])
        self.assertEqual(b.json()["chrono"]["id"], a.json()["chrono"]["id"])
        self.assertEqual(len(self._chronos()), 1)

    def test_sans_client_uuid_rien_ne_change(self):
        a = self._demarrer()
        b = self._demarrer()
        self.assertEqual(a.status_code, 200)
        self.assertEqual(b.status_code, 400, "comme avant : un chrono tourne déjà")
        self.assertEqual(b.json()["error"], "refused")
        self.assertFalse(self._chronos().client_uuid)

    def test_uuid_invalide_est_un_400(self):
        r = self._demarrer("pas-un-uuid")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_client_uuid")
        self.assertFalse(self._chronos())

    def test_le_uuid_d_un_autre_usager_ne_rend_pas_son_chrono(self):
        cle = str(uuid.uuid4())
        a = self._demarrer(cle)
        b = self._post("/chrono/demarrer", {"task_id": self.tache.id, "client_uuid": cle},
                       jeton=self._jeton(self.voisin))
        self.assertEqual(b.status_code, 200, b.text)
        self.assertNotIn("replay", b.json())
        self.assertNotEqual(a.json()["chrono"]["id"], b.json()["chrono"]["id"])
        self.assertEqual(len(self._chronos(self.voisin)), 1)

    def test_demarrage_date_par_at(self):
        il_y_a = fields.Datetime.now() - timedelta(minutes=15)
        r = self._demarrer(str(uuid.uuid4()), at=_ms(il_y_a))
        self.assertEqual(r.status_code, 200, r.text)
        chrono = self._chronos()
        self.assertEqual(chrono.start_time, il_y_a)
        self.assertEqual(chrono.first_start, il_y_a)
        self.assertGreaterEqual(r.json()["chrono"]["elapsed_seconds"], 15 * 60 - 1)

    # ── Viser par timer_uuid ─────────────────────────────────────────

    def test_pause_par_timer_uuid_vise_le_bon_chrono(self):
        cle = str(uuid.uuid4())
        self._demarrer(str(uuid.uuid4()), tache=self.autre_tache)
        self._demarrer(cle)
        r = self._post("/chrono/pause", {"timer_uuid": cle, "client_uuid": str(uuid.uuid4())})
        self.assertEqual(r.status_code, 200, r.text)
        vise = self._chronos().filtered(lambda c: c.client_uuid == cle)
        autre = self._chronos() - vise
        self.assertTrue(vise.is_paused)
        self.assertFalse(autre.is_paused)
        self.assertTrue(vise.paused_at)

    def test_timer_uuid_d_un_autre_usager_est_introuvable(self):
        cle = str(uuid.uuid4())
        self._demarrer(cle)
        r = self._post("/chrono/pause", {"timer_uuid": cle}, jeton=self._jeton(self.voisin))
        self.assertEqual(r.status_code, 404)
        self.assertFalse(self._chronos().is_paused)

    def test_timer_uuid_invalide_est_un_400(self):
        r = self._post("/chrono/pause", {"timer_uuid": "n'importe quoi"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_timer_uuid")

    def test_enregistrer_rejoue_ne_saisit_qu_une_fois(self):
        cle_demarrage = str(uuid.uuid4())
        self._demarrer(cle_demarrage)
        cle = str(uuid.uuid4())
        charge = {"timer_uuid": cle_demarrage, "minutes": 30, "client_uuid": cle}
        a = self._post("/chrono/enregistrer", charge)
        b = self._post("/chrono/enregistrer", charge)
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.text)
        self.assertTrue(b.json()["replay"])
        lignes = self.env["account.analytic.line"].sudo().search(
            [("task_id", "=", self.tache.id), ("user_id", "=", self.personne.id)])
        self.assertEqual(len(lignes), 1, "une seconde feuille de temps doublerait les heures")
        self.assertFalse(self._chronos())

    def test_pause_reprise_abandon_rejoues(self):
        cle_demarrage = str(uuid.uuid4())
        self._demarrer(cle_demarrage)
        for route in ("/chrono/pause", "/chrono/reprendre", "/chrono/abandonner"):
            cle = str(uuid.uuid4())
            a = self._post(route, {"timer_uuid": cle_demarrage, "client_uuid": cle})
            b = self._post(route, {"timer_uuid": cle_demarrage, "client_uuid": cle})
            self.assertEqual(a.status_code, 200, (route, a.text))
            self.assertEqual(b.status_code, 200, (route, b.text))
            self.assertEqual(b.json(), dict(a.json(), replay=True), route)
        self.assertFalse(self._chronos())

    # ── L'heure du geste ─────────────────────────────────────────────

    def _chrono_depuis(self, minutes):
        self._demarrer(str(uuid.uuid4()))
        chrono = self._chronos()
        debut = fields.Datetime.now() - timedelta(minutes=minutes)
        chrono.sudo().write({"start_time": debut, "first_start": debut})
        self.env.flush_all()
        return chrono

    def test_pause_datee_ne_compte_que_jusqu_au_geste(self):
        chrono = self._chrono_depuis(30)
        at = fields.Datetime.now() - timedelta(minutes=10)
        r = self._post("/chrono/pause", {"timer_id": chrono.id, "at": _ms(at)})
        self.assertEqual(r.status_code, 200, r.text)
        chrono.invalidate_recordset()
        self.assertAlmostEqual(chrono.accumulated_seconds, 20 * 60, delta=2)
        self.assertEqual(chrono.paused_at, at)

    def test_reprise_datee_repart_du_geste(self):
        chrono = self._chrono_depuis(30)
        pause = fields.Datetime.now() - timedelta(minutes=20)
        self._post("/chrono/pause", {"timer_id": chrono.id, "at": _ms(pause)})
        reprise = fields.Datetime.now() - timedelta(minutes=5)
        r = self._post("/chrono/reprendre", {"timer_id": chrono.id, "at": _ms(reprise)})
        self.assertEqual(r.status_code, 200, r.text)
        chrono.invalidate_recordset()
        self.assertEqual(chrono.start_time, reprise)
        self.assertFalse(chrono.paused_at)
        # 10 min avant la pause + 5 min depuis la reprise.
        self.assertAlmostEqual(chrono._elapsed_seconds(), 15 * 60, delta=3)

    def test_at_hors_bornes_est_ramene_pas_refuse(self):
        """Relecture adverse : refuser jetait le geste ; on ramène l'heure."""
        maintenant = fields.Datetime.now()
        # Futur : ramené à maintenant.
        chrono = self._chrono_depuis(30)
        r = self._post("/chrono/pause", {"timer_id": chrono.id, "at": _ms(maintenant + timedelta(minutes=5))})
        self.assertEqual(r.status_code, 200, r.text)
        chrono.invalidate_recordset()
        self.assertTrue(chrono.is_paused)
        self.assertLessEqual(chrono.paused_at, fields.Datetime.now())

    def test_at_avant_le_demarrage_est_ramene_au_demarrage(self):
        chrono = self._chrono_depuis(30)
        debut = chrono.start_time
        r = self._post("/chrono/pause", {
            "timer_id": chrono.id, "at": _ms(fields.Datetime.now() - timedelta(minutes=45))})
        self.assertEqual(r.status_code, 200, r.text)
        chrono.invalidate_recordset()
        self.assertGreaterEqual(chrono.paused_at, debut)

    def test_at_illisible_est_un_400(self):
        chrono = self._chrono_depuis(30)
        for brut in ("12:00", 1.5, True):
            with self.subTest(brut=brut):
                r = self._post("/chrono/pause", {"timer_id": chrono.id, "at": brut})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json()["error"], "invalid_at")
        chrono.invalidate_recordset()
        self.assertFalse(chrono.is_paused, "un refus ne pose rien")

    def test_une_pause_d_un_week_end_garde_son_heure(self):
        chrono = self._chrono_depuis(60 * 72)
        pause = fields.Datetime.now() - timedelta(hours=60)
        r = self._post("/chrono/pause", {"timer_id": chrono.id, "at": _ms(pause)})
        self.assertEqual(r.status_code, 200, r.text)
        chrono.invalidate_recordset()
        self.assertEqual(chrono.paused_at, pause.replace(microsecond=0))

    def test_une_minute_d_avance_est_toleree(self):
        at = fields.Datetime.now() + timedelta(minutes=1)
        r = self._demarrer(str(uuid.uuid4()), at=_ms(at))
        self.assertEqual(r.status_code, 200, r.text)

    def test_reprise_avant_la_pause_est_ramenee_a_la_pause(self):
        chrono = self._chrono_depuis(30)
        pause = fields.Datetime.now() - timedelta(minutes=10)
        self._post("/chrono/pause", {"timer_id": chrono.id, "at": _ms(pause)})
        r = self._post("/chrono/reprendre", {
            "timer_id": chrono.id, "at": _ms(pause - timedelta(minutes=1))})
        self.assertEqual(r.status_code, 200, r.text)
        chrono.invalidate_recordset()
        self.assertEqual(chrono.start_time, pause.replace(microsecond=0))

    def test_le_navigateur_garde_sa_signature(self):
        """Les méthodes publiques n'offrent pas ``at`` par RPC."""
        Timer = self.env["bf.timer"].with_user(self.personne)
        donnees = Timer.start_timer(self.autre_tache.id)
        with self.assertRaises(TypeError):
            Timer.pause_timer(donnees["id"], at=fields.Datetime.now())
        Timer.pause_timer(donnees["id"])
        chrono = Timer.browse(donnees["id"])
        self.assertTrue(chrono.is_paused and chrono.paused_at)
        Timer.resume_timer(donnees["id"])
        self.assertFalse(chrono.paused_at)

