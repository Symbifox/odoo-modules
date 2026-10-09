"""Téléverser un fichier média par morceaux, joué par HTTP.

Ce qui est éprouvé : les morceaux s'ajoutent dans l'ordre, un morceau renvoyé
est ignoré, un trou est refusé avec la position où reprendre, et le fichier
complet part au dossier surveillé EN FLUX, sous le nom que le processeur sait
lire. Le WebDAV est un double : rien ne sort du banc.
"""
import json
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import new_test_user
from odoo.tests.common import HttpCase, tagged

BASE = "/bf_capture/mobile/v1"
MO = 1024 * 1024


@tagged("post_install", "-at_install", "bf_capture")
class TestTeleversement(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("bf_capture.folder", "Transcriptions")
        self.env["nextcloud.document.config"].sudo().create({
            "name": "Banc téléversement",
            "nextcloud_base_url": "https://nextcloud.example.com",
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "jdoe",
            "company_id": self.env.company.id,
        })
        self.evenement = self.env["calendar.event"].create({
            "name": "Conseil du banc",
            "start": datetime(2026, 9, 21, 19, 0, 0),
            "stop": datetime(2026, 9, 21, 20, 0, 0),
        })
        self.usager = self.env.ref("base.user_admin")
        self.poses = []
        Config = type(self.env["nextcloud.document.config"])

        def faux_put(config, path, content, content_type=""):
            # En flux : la passerelle reçoit un fichier ouvert, pas des octets.
            self.poses.append((path, content.read() if hasattr(content, "read") else content))

        for nom, double in (("_webdav_propfind", lambda *a, **k: []),
                            ("_webdav_mkcol", lambda *a, **k: True),
                            ("_webdav_put", faux_put)):
            rustine = patch.object(Config, nom, double)
            rustine.start()
            self.addCleanup(rustine.stop)
        rustine = patch("odoo.addons.bf_capture.controllers.mobile_api._device",
                        lambda: SimpleNamespace(user_id=self.usager, _fields={}))
        rustine.start()
        self.addCleanup(rustine.stop)

    def _json(self, route, corps):
        return self.url_open(f"{BASE}{route}", data=json.dumps(corps),
                             headers={"Content-Type": "application/json"})

    def _morceau(self, upload_id, offset, octets):
        return self.url_open(f"{BASE}/televersement/morceau?upload_id={upload_id}&offset={offset}",
                             data=octets, headers={"Content-Type": "application/octet-stream"})

    def _ouvrir(self, nom="reunion.mp4", taille=int(2.5 * MO)):
        reponse = self._json("/televersement/ouvrir", {"nom": nom, "taille": taille,
                                                        "event_id": self.evenement.id})
        self.assertEqual(reponse.status_code, 200, reponse.text)
        return reponse.json()["upload_id"]

    def test_la_sonde_annonce_le_televersement(self):
        charge = self.url_open(f"{BASE}/ping").json()
        self.assertEqual(charge["televersement"], 1)
        self.assertGreater(charge["televersement_max_bytes"], 128 * MO)
        self.assertEqual(charge["morceau_bytes"], 8 * MO)

    def test_les_morceaux_se_reprennent_et_le_fichier_part_en_flux(self):
        contenu = bytes(range(256)) * (int(2.5 * MO) // 256)
        uid = self._ouvrir(taille=len(contenu))
        self.assertEqual(self._morceau(uid, 0, contenu[:MO]).json()["recus"], MO)
        self.assertEqual(self._morceau(uid, MO, contenu[MO:2 * MO]).json()["recus"], 2 * MO)
        # Renvoyé après une réponse perdue : ignoré, rien ne double.
        self.assertEqual(self._morceau(uid, 0, contenu[:MO]).json()["recus"], 2 * MO)
        # Trop loin : refusé, avec la position où reprendre.
        trou = self._morceau(uid, 3 * MO, b"x")
        self.assertEqual(trou.status_code, 409)
        self.assertEqual(trou.json()["recus"], 2 * MO)
        # Terminer trop tôt : même réponse, rien ne part.
        tot = self._json("/televersement/terminer", {"upload_id": uid})
        self.assertEqual(tot.status_code, 409)
        self.assertEqual(self.poses, [])
        # L'état dit où on en est, puis le dernier morceau.
        etat = self.url_open(f"{BASE}/televersement/etat?upload_id={uid}").json()
        self.assertEqual((etat["recus"], etat["taille"]), (2 * MO, len(contenu)))
        self._morceau(uid, 2 * MO, contenu[2 * MO:])

        fin = self._json("/televersement/terminer", {"upload_id": uid})
        self.assertEqual(fin.status_code, 200, fin.text)
        self.assertEqual(fin.json()["nom"], "2026-09-21 15-00-00 - Conseil du banc.mp4")
        self.assertEqual(len(self.poses), 1)
        chemin, octets = self.poses[0]
        self.assertEqual(chemin, "Transcriptions/2026-09-21 15-00-00 - Conseil du banc.mp4")
        self.assertEqual(octets, contenu, "le fichier arrive entier et dans l'ordre")
        # Le fichier du banc est effacé une fois versé.
        self.assertEqual(self.url_open(f"{BASE}/televersement/etat?upload_id={uid}").status_code, 400)

    def test_terminer_deux_fois_ne_depose_qu_une_fois(self):
        uid = self._ouvrir(taille=10)
        self._morceau(uid, 0, b"0123456789")
        cle = str(uuid.uuid4())
        premier = self._json("/televersement/terminer", {"upload_id": uid, "client_uuid": cle})
        second = self._json("/televersement/terminer", {"upload_id": uid, "client_uuid": cle})
        self.assertEqual(premier.status_code, 200, premier.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json().get("replay"))
        self.assertEqual(len(self.poses), 1)

    def test_ce_que_le_processeur_ne_lit_pas_est_refuse_avant_le_premier_octet(self):
        reponse = self._json("/televersement/ouvrir", {"nom": "rapport.pdf", "taille": 10,
                                                        "event_id": self.evenement.id})
        self.assertEqual(reponse.status_code, 400)

    def test_au_dela_du_plafond_est_refuse(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_capture.televersement_max_bytes", "100")
        reponse = self._json("/televersement/ouvrir", {"nom": "a.mp3", "taille": 101,
                                                        "event_id": self.evenement.id})
        self.assertEqual(reponse.status_code, 400)

    def test_sans_rencontre_ni_titre_est_refuse(self):
        reponse = self._json("/televersement/ouvrir", {"nom": "a.mp3", "taille": 10})
        self.assertEqual(reponse.status_code, 400)

    def test_un_nombre_illisible_est_un_refus_pas_une_panne(self):
        uid = self._ouvrir(taille=10)
        self.assertEqual(self._morceau(uid, "abc", b"x").status_code, 400)
        reponse = self._json("/televersement/ouvrir", {"nom": "a.mp3", "taille": "beaucoup",
                                                        "event_id": self.evenement.id})
        self.assertEqual(reponse.status_code, 400)

    def test_un_identifiant_fabrique_ne_sort_pas_du_dossier(self):
        reponse = self.url_open(f"{BASE}/televersement/etat?upload_id=../../etc/passwd")
        self.assertEqual(reponse.status_code, 400)

    def test_le_televersement_d_un_autre_est_introuvable(self):
        uid = self._ouvrir(taille=10)
        self.usager = new_test_user(self.env, login="capture_autre_morceaux", groups="base.group_user")
        self.assertEqual(self._morceau(uid, 0, b"0123456789").status_code, 403)
        self.assertEqual(self._json("/televersement/terminer", {"upload_id": uid}).status_code, 403)
