"""Réveil par push : les gardes des deux autres transports, et le chiffrement.

🔴 ``_push`` n'avait d'abord AUCUNE des gardes des transports SMS et courriel.
Le jeton de publication ntfy, secret serveur, partait vers l'hôte de n'importe
quel endpoint inscrit ; les redirections étaient suivies et l'endpoint jamais
revérifié à l'envoi. Chaque essai ici porte la valeur qui ferait passer l'ancien
envoi.

Aucun essai ne pousse vraiment : ``requests.post`` est un espion.
"""

import base64
import json
import os
from unittest.mock import patch

import http_ece
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.bf_sms_archive.models.push_transport import SmsUnifiedPush

from ..controllers import pbx_api
from ..models import wake_selftest


def _b64(octets):
    return base64.urlsafe_b64encode(octets).decode().rstrip("=")


def _unb64(texte):
    return base64.urlsafe_b64decode(texte + "=" * (-len(texte) % 4))


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.text = ""


@tagged("post_install", "-at_install")
class TestWakeGardes(TransactionCase):

    def setUp(self):
        super().setUp()
        pbx_api._HITS.clear()
        self.user = self.env["res.users"].create({
            "name": "Poste gardes",
            "login": "poste-gardes-wake",
            "sip_extension": "1092",
            "sip_enabled": True,
        })
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_sms_archive.ntfy_publish_token", "tk-banc")
        icp.set_param("bf_sms_archive.ntfy_base_url", "https://ntfy.test")
        self.prive = ec.generate_private_key(ec.SECP256R1())
        self.p256dh = _b64(self.prive.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint))
        self.auth = _b64(os.urandom(16))

    def _device(self, endpoint, cles=False):
        vals = {
            "user_id": self.user.id,
            "device_token": "jeton-%s" % endpoint,
            "push_endpoint": endpoint,
            "last_seen": fields.Datetime.now(),
        }
        if cles:
            vals.update(push_p256dh=self.p256dh, push_auth=self.auth)
        return self.env["sms.archive.mobile.device"].sudo().create(vals)

    def _pousser(self, public=lambda url: True):
        ctrl = pbx_api.BfSoftphonePbxApi()

        class _R:
            env = self.env

        envois = {}

        def espion(url, **kw):
            envois[url] = kw
            return FakeResponse()

        with patch.object(pbx_api, "request", _R()), \
                patch.object(pbx_api, "safe_push_endpoint", side_effect=public), \
                patch.object(pbx_api.requests, "post", side_effect=espion):
            envoyes = ctrl._push(self.user, {"type": "call", "ext": "1092",
                                             "peer": "5145550100"})
        return envoyes, envois

    def test_le_jeton_ntfy_ne_part_que_vers_notre_hote(self):
        """🔴 Le secret serveur ne suit pas un endpoint arbitraire."""
        self._device("https://ntfy.test/upNous")
        self._device("https://hote-attaquant.example/upEux")
        envoyes, envois = self._pousser()
        self.assertEqual(envoyes, 2)
        self.assertEqual(envois["https://ntfy.test/upNous"]["headers"]["Authorization"],
                         "Bearer tk-banc")
        self.assertNotIn("Authorization",
                         envois["https://hote-attaquant.example/upEux"]["headers"])

    def test_aucune_redirection_suivie(self):
        """🔴 Un 30x enverrait le POST, jeton compris, ailleurs."""
        self._device("https://ntfy.test/upNous")
        _envoyes, envois = self._pousser()
        self.assertIs(envois["https://ntfy.test/upNous"]["allow_redirects"], False)

    def test_une_redirection_n_est_pas_une_remise(self):
        self._device("https://ntfy.test/upNous")
        ctrl = pbx_api.BfSoftphonePbxApi()

        class _R:
            env = self.env

        with patch.object(pbx_api, "request", _R()), \
                patch.object(pbx_api, "safe_push_endpoint", return_value=True), \
                patch.object(pbx_api.requests, "post", return_value=FakeResponse(302)):
            self.assertEqual(ctrl._push(self.user, {"type": "call"}), 0)

    def test_un_endpoint_devenu_prive_est_ecarte_a_l_envoi(self):
        """🔴 Revérifié à l'envoi, pas seulement à l'inscription. Et
        écarté sans être purgé : une résolution ratée pendant un appel ne doit
        pas désinscrire le téléphone."""
        interne = self._device("https://repointe.example/upA")
        self._device("https://ntfy.test/upNous")
        envoyes, envois = self._pousser(public=lambda url: "repointe" not in url)
        self.assertEqual(envoyes, 1)
        self.assertEqual(list(envois), ["https://ntfy.test/upNous"])
        self.assertEqual(interne.push_endpoint, "https://repointe.example/upA")

    def test_la_garde_anti_ssrf_n_est_pas_une_maquette(self):
        """La vraie fonction, sans patch : une adresse interne est refusée."""
        for url in ("http://127.0.0.1:8080/up", "http://169.254.169.254/latest",
                    "http://10.0.0.5/up", "ftp://ntfy.test/up", "pas-une-url"):
            with self.subTest(url=url):
                self.assertFalse(pbx_api.safe_push_endpoint(url))

    def test_le_reveil_est_chiffre_pour_l_appareil_qui_a_ses_cles(self):
        self._device("https://ntfy.test/upNeuf", cles=True)
        self._device("https://ntfy.test/upVieux")
        envoyes, envois = self._pousser()
        self.assertEqual(envoyes, 2)
        neuf = envois["https://ntfy.test/upNeuf"]
        self.assertEqual(neuf["headers"]["Content-Encoding"], "aes128gcm")
        self.assertEqual(neuf["headers"]["Content-Type"], "application/octet-stream")
        self.assertEqual(neuf["headers"]["TTL"], "60",
                         "Un réveil gardé une journée sonnerait après le raccroché.")
        self.assertEqual(neuf["headers"]["Authorization"], "Bearer tk-banc")
        self.assertNotIn(b"5145550100", neuf["data"])
        clair = http_ece.decrypt(neuf["data"], private_key=self.prive,
                                 auth_secret=_unb64(self.auth), version="aes128gcm")
        self.assertEqual(json.loads(clair)["type"], "call")
        self.assertEqual(json.loads(clair)["peer"], "5145550100")
        vieux = envois["https://ntfy.test/upVieux"]
        self.assertEqual(json.loads(vieux["data"])["type"], "call")
        self.assertEqual(vieux["headers"]["Content-Type"], "application/json")
        self.assertNotIn("Content-Encoding", vieux["headers"])

    def test_cibles_porte_les_cles_lues_d_avance(self):
        """Les fils d'envoi n'ont pas le droit à l'ORM : les clés doivent déjà
        être dans ce que ``cibles`` rend."""
        self._device("https://ntfy.test/upNeuf", cles=True)
        cible, = pbx_api.cibles(self.env, self.user)
        self.assertEqual(cible.endpoint, "https://ntfy.test/upNeuf")
        self.assertEqual((cible.p256dh, cible.auth), (self.p256dh, self.auth))

    def test_call_est_annonce_par_le_softphone_seulement(self):
        """« call » est chiffré par CE module : il l'annonce, et la définition
        de bf_sms_archive seule ne l'annonce pas."""
        types = self.env["sms.archive.unifiedpush"]._webpush_types()
        for attendu in ("sms", "clear", "clear_all", "genfox", "call"):
            self.assertIn(attendu, types)
        self.assertNotIn(
            "call", SmsUnifiedPush._webpush_types(self.env["sms.archive.unifiedpush"]))


@tagged("post_install", "-at_install")
class TestWakeSelftestGardes(TransactionCase):
    """La sonde canari suit la même définition (``cibles``) et la même règle de
    jeton que le vrai réveil."""

    def setUp(self):
        super().setUp()
        self.user = self.env["res.users"].create({
            "name": "Poste canari",
            "login": "poste-canari-wake",
            "sip_extension": "1093",
            "sip_enabled": True,
        })
        # Les autres postes de la base ne doivent pas brouiller le verdict.
        self.env["res.users"].sudo().search([
            ("sip_enabled", "=", True), ("id", "!=", self.user.id),
        ]).write({"sip_enabled": False})
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("bf_softphone.wake_enabled", "1")
        icp.set_param("bf_sms_archive.ntfy_publish_token", "tk-banc")
        icp.set_param("bf_sms_archive.ntfy_base_url", "https://ntfy.test")

    def _device(self, endpoint):
        return self.env["sms.archive.mobile.device"].sudo().create({
            "user_id": self.user.id,
            "device_token": "jeton-canari-%s" % endpoint,
            "push_endpoint": endpoint,
            "last_seen": fields.Datetime.now(),
        })

    def _sonder(self, statut=200):
        vus = []

        def espion(url, **kw):
            vus.append((url, kw))
            return FakeResponse(statut)

        # ⚠️ La garde anti-SSRF de la sonde est neutralisée ICI, et seulement
        # ici : le banc n'a pas de DNS pour `ntfy.test`, donc elle écarterait
        # tout et ces essais-là ne mesureraient plus rien. Ils portent sur le
        # JETON et sur l'HÔTE. La garde elle-même est éprouvée pour de vrai
        # dans `test_gardes_rpc.test_la_sonde_ecarte_un_endpoint_non_public`.
        # Même convention que `TestWakePush.setUp` pour le vrai réveil.
        with patch.object(wake_selftest, "safe_push_endpoint", return_value=True), \
                patch.object(wake_selftest.requests, "post", side_effect=espion):
            verdict = self.env["res.users"].softphone_wake_selftest()
        return verdict, vus

    def test_la_sonde_publie_avec_le_jeton_sur_notre_ntfy(self):
        self._device("https://ntfy.test/upAbc")
        verdict, vus = self._sonder()
        self.assertEqual(verdict["etat"], "up", verdict)
        url, kw = vus[0]
        self.assertEqual(url, "https://ntfy.test/%s" % wake_selftest.SUJET_CANARI)
        self.assertEqual(kw["headers"]["Authorization"], "Bearer tk-banc")
        self.assertIs(kw["allow_redirects"], False)

    def test_la_sonde_ne_prete_pas_le_jeton_a_un_hote_etranger(self):
        self._device("https://ailleurs.example/upAbc")
        verdict, vus = self._sonder(statut=403)
        self.assertNotIn("Authorization", vus[0][1]["headers"])
        self.assertEqual(verdict["etat"], "down")
        self.assertIn("hors de l'hôte", verdict["detail"])

    def test_une_redirection_est_une_panne_pour_la_sonde(self):
        self._device("https://ntfy.test/upAbc")
        verdict, _vus = self._sonder(statut=301)
        self.assertEqual(verdict["etat"], "down")
