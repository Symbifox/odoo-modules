"""Le rejeu de la file hors ligne, et la panne qui n'est pas un refus.

L'app rejoue tant qu'elle n'a pas de réponse. Une requête arrivée dont la
réponse s'est perdue revient donc avec le MÊME ``client_uuid`` : elle doit
retrouver sa première réponse, pas déposer un second fichier (qui ferait un
second compte rendu). Et une panne de Nextcloud doit sortir en 503, pas en
400 : sur un 400, l'app retire l'enregistrement de sa file.
"""

import io
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import UserError
from odoo.tests.common import HttpCase, new_test_user, tagged

BASE = "/bf_capture/mobile/v1"
PASSERELLE = "odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config"


@tagged("post_install", "-at_install", "bf_capture")
class TestCaptureIdempotence(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("bf_capture.folder", "Transcriptions")
        self.env["nextcloud.document.config"].sudo().create({
            "name": "Banc idempotence",
            "nextcloud_base_url": "https://nextcloud.example.com",
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "jdoe",
            "company_id": self.env.company.id,
        })
        self.usager = self.env.ref("base.user_admin")
        self.autre = new_test_user(self.env, login="capture_autre_rejeu",
                                   groups="base.group_user")
        self.evenement = self.env["calendar.event"].create({
            "name": "Statutaire idempotent",
            "start": datetime(2026, 9, 21, 19, 0, 0),
            "stop": datetime(2026, 9, 21, 20, 0, 0),
        })
        self.poses = []
        Config = type(self.env["nextcloud.document.config"])
        for cible, double in (
            ("_webdav_propfind", lambda *a, **k: []),
            ("_webdav_mkcol", lambda *a, **k: True),
            ("_webdav_put", lambda s, path, content, content_type="":
                self.poses.append(path)),
        ):
            rustine = patch.object(Config, cible, double)
            rustine.start()
            self.addCleanup(rustine.stop)
        rustine = patch.object(type(self.env["bf.capture"]),
                               "transcription_disponible", lambda self: False)
        rustine.start()
        self.addCleanup(rustine.stop)

    def _au_nom_de(self, usager):
        return patch(
            "odoo.addons.bf_capture.controllers.mobile_api._device",
            lambda: SimpleNamespace(user_id=usager, _fields={}),
        )

    def _rencontre(self, cle=None, usager=None):
        data = {"event_id": str(self.evenement.id)}
        if cle is not None:
            data["client_uuid"] = cle
        with self._au_nom_de(usager or self.usager):
            return self.url_open(
                f"{BASE}/rencontre", data=data,
                files={"audio": ("capture.m4a", io.BytesIO(b"x" * 4000), "audio/mp4")})

    def _memo(self, cle=None, usager=None, titre="Mémo idempotent"):
        data = {"titre": titre}
        if cle is not None:
            data["client_uuid"] = cle
        with self._au_nom_de(usager or self.usager):
            return self.url_open(
                f"{BASE}/memo", data=data,
                files={"audio": ("memo.m4a", io.BytesIO(b"x" * 3000), "audio/mp4")})

    def _notes(self, titre="Mémo idempotent"):
        return self.env["bf.note"].sudo().search_count([("name", "=", titre)])

    # ── Le rejeu ──────────────────────────────────────────────────────

    def test_rencontre_rejouee_ne_depose_qu_un_fichier(self):
        cle = str(uuid.uuid4())
        premiere = self._rencontre(cle)
        seconde = self._rencontre(cle)
        self.assertEqual(premiere.status_code, 200, premiere.text)
        self.assertEqual(seconde.status_code, 200, seconde.text)
        self.assertEqual(len(self.poses), 1, "un second fichier ferait un second compte rendu")
        attendu = dict(premiere.json(), replay=True)
        self.assertEqual(seconde.json(), attendu)
        self.assertNotIn("replay", premiere.json())

    def test_memo_rejoue_ne_cree_qu_une_note(self):
        cle = str(uuid.uuid4())
        premiere = self._memo(cle)
        seconde = self._memo(cle.upper())  # même UUID, autre graphie
        self.assertEqual(premiere.status_code, 200, premiere.text)
        self.assertEqual(self._notes(), 1)
        self.assertEqual(seconde.json(), dict(premiere.json(), replay=True))
        self.assertEqual(self.env["bf.capture.mobile.receipt"].search_count(
            [("user_id", "=", self.usager.id)]), 1)

    def test_sans_client_uuid_rien_ne_change(self):
        premiere = self._memo()
        seconde = self._memo()
        self.assertEqual((premiere.status_code, seconde.status_code), (200, 200))
        self.assertEqual(self._notes(), 2, "une ancienne app n'a pas de garde, comme avant")
        self.assertNotIn("replay", seconde.json())
        self.assertFalse(self.env["bf.capture.mobile.receipt"].search_count([]))

    def test_uuid_invalide_est_un_400_et_ne_fait_rien(self):
        reponse = self._memo("pas-un-uuid")
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(reponse.json()["error"], "invalid_client_uuid")
        self.assertEqual(self._notes(), 0)
        reponse = self._rencontre("1234")
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(self.poses, [])

    def test_le_uuid_d_un_autre_usager_ne_rend_pas_sa_reponse(self):
        cle = str(uuid.uuid4())
        a = self._memo(cle)
        b = self._memo(cle, usager=self.autre)
        self.assertEqual(b.status_code, 200, b.text)
        self.assertNotIn("replay", b.json())
        self.assertNotEqual(a.json()["note_id"], b.json()["note_id"])
        self.assertEqual(self._notes(), 2)

    def test_meme_uuid_sur_une_autre_route_est_refuse(self):
        cle = str(uuid.uuid4())
        self.assertEqual(self._memo(cle).status_code, 200)
        reponse = self._rencontre(cle)
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(self.poses, [])

    def test_un_refus_ne_pose_pas_d_accuse(self):
        """Un 400 retire le geste de la file : rien à retrouver ensuite."""
        cle = str(uuid.uuid4())
        with self._au_nom_de(self.usager):
            refus = self.url_open(f"{BASE}/memo", data={"client_uuid": cle})
        self.assertEqual(refus.status_code, 400)
        self.assertFalse(self.env["bf.capture.mobile.receipt"].search_count([]))
        reprise = self._memo(cle)
        self.assertEqual(reprise.status_code, 200)
        self.assertNotIn("replay", reprise.json())

    def test_les_accuses_de_plus_de_trente_jours_sont_purges(self):
        Recu = self.env["bf.capture.mobile.receipt"]
        vieux = Recu._record(self.usager.id, str(uuid.uuid4()), "/memo", "{}")
        recent = Recu._record(self.usager.id, str(uuid.uuid4()), "/memo", "{}")
        self.env.cr.execute(
            "UPDATE bf_capture_mobile_receipt SET create_date = %s WHERE id = %s",
            (datetime.utcnow() - timedelta(days=31), vieux.id))
        Recu.invalidate_model()
        Recu._gc_old_receipts()
        self.assertFalse(vieux.exists())
        self.assertTrue(recent.exists())


@tagged("post_install", "-at_install", "bf_capture")
class TestCapturePanneNextcloud(HttpCase):
    """La passerelle Nextcloud réelle, avec ``requests`` remplacé par la panne."""

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("bf_capture.folder", "Transcriptions")
        self.env["nextcloud.document.config"].sudo().create({
            "name": "Banc panne",
            "nextcloud_base_url": "https://nextcloud.example.com",
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "jdoe",
            "company_id": self.env.company.id,
        })
        self.evenement = self.env["calendar.event"].create({
            "name": "Rencontre pendant la panne",
            "start": datetime(2026, 9, 21, 19, 0, 0),
            "stop": datetime(2026, 9, 21, 20, 0, 0),
        })
        Config = type(self.env["nextcloud.document.config"])
        for cible, double in (
            ("_get_auth", lambda self: ("jdoe", "secret")),
            ("_webdav_propfind", lambda *a, **k: []),
            ("_webdav_mkcol", lambda *a, **k: True),
        ):
            rustine = patch.object(Config, cible, double)
            rustine.start()
            self.addCleanup(rustine.stop)
        rustine = patch(
            "odoo.addons.bf_capture.controllers.mobile_api._device",
            lambda: SimpleNamespace(user_id=self.env.ref("base.user_admin"), _fields={}))
        rustine.start()
        self.addCleanup(rustine.stop)

    def _deposer(self, cle):
        return self.url_open(
            f"{BASE}/rencontre",
            data={"event_id": str(self.evenement.id), "client_uuid": cle},
            files={"audio": ("capture.m4a", io.BytesIO(b"x" * 4000), "audio/mp4")})

    def _verifier_503(self, **put):
        cle = str(uuid.uuid4())
        with patch(f"{PASSERELLE}.requests.put", **put):
            reponse = self._deposer(cle)
        self.assertEqual(reponse.status_code, 503, reponse.text)
        self.assertEqual(reponse.json(), {"error": "unavailable"})
        self.assertFalse(self.env["bf.capture.mobile.receipt"].search_count(
            [("client_uuid", "=", cle)]), "une panne ne pose pas d'accusé")
        return cle

    def test_connexion_refusee_est_un_503(self):
        self._verifier_503(side_effect=requests.ConnectionError("Connection refused"))

    def test_delai_depasse_est_un_503(self):
        self._verifier_503(side_effect=requests.Timeout("read timed out"))

    def test_nextcloud_en_5xx_est_un_503(self):
        self._verifier_503(return_value=MagicMock(status_code=503))

    def test_apres_la_panne_le_rejeu_depose_pour_de_vrai(self):
        cle = self._verifier_503(side_effect=requests.ConnectionError("down"))
        with patch(f"{PASSERELLE}.requests.put",
                   return_value=MagicMock(status_code=201)) as put:
            reponse = self._deposer(cle)
        self.assertEqual(reponse.status_code, 200, reponse.text)
        self.assertNotIn("replay", reponse.json())
        self.assertEqual(put.call_count, 1)

    def test_instance_sans_dossier_reste_un_400(self):
        """Une vraie erreur de configuration côté saisie reste un refus."""
        self.env["nextcloud.document.config"].sudo().search([]).write({"active": False})
        reponse = self._deposer(str(uuid.uuid4()))
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(reponse.json()["error"], "bad_request")

    def test_dictee_indisponible_est_un_503(self):
        Capture = type(self.env["bf.capture"])
        with patch.object(Capture, "transcription_disponible", lambda self: True), \
                patch.object(Capture, "_transcrire", side_effect=UserError(
                    "Le service de transcription est injoignable.")):
            reponse = self.url_open(
                f"{BASE}/memo", data={"client_uuid": str(uuid.uuid4())},
                files={"audio": ("memo.m4a", io.BytesIO(b"x" * 3000), "audio/mp4")})
        self.assertEqual(reponse.status_code, 503, reponse.text)
        self.assertEqual(reponse.json()["error"], "unavailable")
