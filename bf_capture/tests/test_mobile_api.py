"""La surface mobile, jouée par HTTP pour de vrai.

Les essais du moteur ([test_capture]) ne disent rien de la porte : un contrôleur
peut refuser un jeton valide, rendre une page d'erreur Odoo au lieu de JSON, ou
laisser passer un compte portail. Ici, on frappe les routes.

⚠️ Aucun module de messagerie n'est installé sur ce banc, donc aucun modèle
d'appareil n'existe : `_device` est remplacé par un double pour la moitié
authentifiée des essais. Ce qui est éprouvé alors, c'est la route et son
contrat, pas la résolution du jeton, qui appartient au module qui l'a émis.
"""

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests.common import HttpCase, tagged

from ..controllers.mobile_api import _appareil_acceptable

BASE = "/bf_capture/mobile/v1"


@tagged("post_install", "-at_install", "bf_capture")
class TestCaptureMobileApi(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("bf_capture.folder", "Transcriptions")
        # Une instance configurée, comme en service : sans configuration
        # Nextcloud, toutes les routes de dépôt répondraient d'abord « aucun
        # dossier », ce qui masquerait ce que ces essais veulent éprouver.
        self.config = self.env["nextcloud.document.config"].sudo().create({
            "name": "Banc HTTP",
            "nextcloud_base_url": "https://nextcloud.example.com",
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "jdoe",
            "company_id": self.env.company.id,
        })

    def _faux_device(self):
        """Un appareil qui répond comme le vrai, sans le module qui l'émet."""
        utilisateur = self.env.ref("base.user_admin")
        return SimpleNamespace(user_id=utilisateur, _fields={})

    def _authentifie(self):
        return patch(
            "odoo.addons.bf_capture.controllers.mobile_api._device",
            lambda: self._faux_device(),
        )

    # ── Qui a le droit de déposer ─────────────────────────────────────

    def test_appareil_dun_usager_interne_est_accepte(self):
        usager = SimpleNamespace(active=True, share=False)
        self.assertTrue(_appareil_acceptable(SimpleNamespace(user_id=usager)))

    def test_appareil_sans_jeton_est_refuse(self):
        self.assertFalse(_appareil_acceptable(None))

    def test_appareil_dun_usager_archive_est_refuse(self):
        """Le coupe-circuit du départ : le jeton peut survivre à la personne."""
        usager = SimpleNamespace(active=False, share=False)
        self.assertFalse(_appareil_acceptable(SimpleNamespace(user_id=usager)))

    def test_appareil_dun_compte_portail_est_refuse(self):
        """🔴 Le dépôt écrit hors des droits de l'appelant, dans un dossier que
        le processeur vide : un compte portail n'y a rien à faire."""
        usager = SimpleNamespace(active=True, share=True)
        self.assertFalse(_appareil_acceptable(SimpleNamespace(user_id=usager)))

    # ── Capacité ──────────────────────────────────────────────────────

    def test_ping_repond_sans_jeton(self):
        """Sans cette sonde, l'app n'affiche jamais l'enregistreur."""
        reponse = self.url_open(f"{BASE}/ping")
        self.assertEqual(reponse.status_code, 200)
        charge = reponse.json()
        self.assertTrue(charge["ok"])
        self.assertEqual(charge["module"], "bf_capture")
        self.assertIn("enabled", charge)
        self.assertIn("max_bytes", charge)
        self.assertEqual(charge["dossier"], "Transcriptions")

    def test_ping_dit_non_sans_configuration(self):
        self.env["nextcloud.document.config"].sudo().search([]).write({"active": False})
        self.assertFalse(self.url_open(f"{BASE}/ping").json()["enabled"])

    # ── Garde ─────────────────────────────────────────────────────────

    def test_cibles_sans_jeton_est_refuse(self):
        reponse = self.url_open(f"{BASE}/cibles")
        self.assertEqual(reponse.status_code, 401)
        self.assertEqual(reponse.json()["error"], "unauthorized")

    def test_depot_sans_jeton_est_refuse(self):
        reponse = self.url_open(
            f"{BASE}/rencontre",
            files={"audio": ("a.m4a", io.BytesIO(b"x" * 10), "audio/mp4")},
        )
        self.assertEqual(reponse.status_code, 401)

    def test_memo_sans_jeton_est_refuse(self):
        reponse = self.url_open(
            f"{BASE}/memo",
            files={"audio": ("a.m4a", io.BytesIO(b"x" * 10), "audio/mp4")},
        )
        self.assertEqual(reponse.status_code, 401)

    # ── Les routes, jouées ────────────────────────────────────────────

    def test_cibles_rend_les_rencontres_de_la_fenetre(self):
        from datetime import datetime, timedelta
        depart = datetime.utcnow() + timedelta(hours=1)
        self.env["calendar.event"].create({
            "name": "Rencontre du banc",
            "start": depart,
            "stop": depart + timedelta(hours=1),
        })
        with self._authentifie():
            reponse = self.url_open(f"{BASE}/cibles")
        self.assertEqual(reponse.status_code, 200)
        titres = [c["titre"] for c in reponse.json()["cibles"]]
        self.assertIn("Rencontre du banc", titres)

    def test_erreur_metier_revient_en_json_pas_en_page_odoo(self):
        """🔴 Un UserError non attrapé rendrait du HTML : le téléphone y lirait
        une erreur de transport là où le serveur a une phrase à dire."""
        with self._authentifie():
            reponse = self.url_open(
                f"{BASE}/rencontre",
                data={"event_id": "999999999"},
                files={"audio": ("a.m4a", io.BytesIO(b"x" * 10), "audio/mp4")},
            )
        self.assertEqual(reponse.status_code, 400)
        charge = reponse.json()
        self.assertEqual(charge["error"], "bad_request")
        self.assertIn("introuvable", charge["detail"])

    def test_depot_sans_titre_dit_pourquoi(self):
        with self._authentifie():
            reponse = self.url_open(
                f"{BASE}/rencontre",
                files={"audio": ("a.m4a", io.BytesIO(b"x" * 10), "audio/mp4")},
            )
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("titre", reponse.json()["detail"])

    def test_fichier_absent_est_refuse(self):
        with self._authentifie():
            reponse = self.url_open(f"{BASE}/memo", data={"titre": "Sans son"})
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("audio", reponse.json()["detail"])

    def test_format_non_audio_est_refuse_tot(self):
        with self._authentifie():
            reponse = self.url_open(
                f"{BASE}/memo",
                files={"audio": ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            )
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("Format", reponse.json()["detail"])

    def test_memo_cree_une_note_et_rend_son_identifiant(self):
        with self._authentifie(), patch.object(
            type(self.env["bf.capture"]), "transcription_disponible", lambda self: False,
        ):
            reponse = self.url_open(
                f"{BASE}/memo",
                data={"titre": "Rappeler l'équipe"},
                files={"audio": ("memo.m4a", io.BytesIO(b"x" * 3000), "audio/mp4")},
            )
        self.assertEqual(reponse.status_code, 200)
        charge = reponse.json()
        self.assertTrue(charge["ok"])
        note = self.env["bf.note"].browse(charge["note_id"])
        self.assertTrue(note.exists())
        self.assertEqual(note.name, "Rappeler l'équipe")
        self.assertFalse(charge["transcrit"])

    def test_depot_de_rencontre_va_jusqu_au_webdav(self):
        """Le chemin complet, avec le WebDAV remplacé par un double."""
        from datetime import datetime
        evenement = self.env["calendar.event"].create({
            "name": "Statutaire du banc",
            "start": datetime(2026, 9, 21, 19, 0, 0),
            "stop": datetime(2026, 9, 21, 20, 0, 0),
        })
        poses = []
        Config = type(self.env["nextcloud.document.config"])
        with self._authentifie(), \
            patch.object(Config, "_webdav_propfind", lambda *a, **k: []), \
            patch.object(Config, "_webdav_mkcol", lambda *a, **k: True), \
            patch.object(
                Config, "_webdav_put",
                lambda s, path, content, content_type="": poses.append((path, len(content))),
            ):
            reponse = self.url_open(
                f"{BASE}/rencontre",
                data={"event_id": str(evenement.id)},
                files={"audio": ("capture.m4a", io.BytesIO(b"x" * 5000), "audio/mp4")},
            )
        self.assertEqual(reponse.status_code, 200, reponse.text)
        charge = reponse.json()
        self.assertEqual(charge["nom"], "2026-09-21 15-00-00 - Statutaire du banc.m4a")
        self.assertEqual(
            poses,
            [("Transcriptions/2026-09-21 15-00-00 - Statutaire du banc.m4a", 5000)],
        )

    def test_chemin_refuse_par_la_passerelle_revient_en_phrase(self):
        """🔴 La passerelle Nextcloud lève une ValidationError, pas une UserError.

        Un dossier mal configuré (ici une séquence %XX, que le nettoyeur de
        chemin refuse par conception) doit revenir au téléphone en phrase
        lisible, pas en « erreur serveur ».
        """
        from datetime import datetime
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_capture.folder", "Transcriptions%20essai")
        evenement = self.env["calendar.event"].create({
            "name": "Rencontre du chemin",
            "start": datetime(2026, 9, 21, 19, 0, 0),
            "stop": datetime(2026, 9, 21, 20, 0, 0),
        })
        with self._authentifie():
            reponse = self.url_open(
                f"{BASE}/rencontre",
                data={"event_id": str(evenement.id)},
                files={"audio": ("capture.m4a", io.BytesIO(b"x" * 3000), "audio/mp4")},
            )
        self.assertEqual(reponse.status_code, 400, reponse.text)
        self.assertEqual(reponse.json()["error"], "bad_request")

    def test_reponse_est_du_json_meme_en_erreur_serveur(self):
        """Une panne inattendue ne doit pas rendre la page d'erreur d'Odoo."""
        with self._authentifie(), patch.object(
            type(self.env["bf.capture"]), "deposer_memo",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("panne")),
        ):
            reponse = self.url_open(
                f"{BASE}/memo",
                files={"audio": ("memo.m4a", io.BytesIO(b"x" * 3000), "audio/mp4")},
            )
        self.assertEqual(reponse.status_code, 500)
        self.assertEqual(json.loads(reponse.text)["error"], "server_error")
