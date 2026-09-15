# -*- coding: utf-8 -*-
"""Poussées chiffrées en WebPush (RFC 8291), audit du 2026-09-08 (C-M3).

Ce qui se joue : le texte d'un SMS et la réponse de Gen transitaient en clair
par ntfy, et quiconque connaissait l'endpoint d'un appareil pouvait lui poster
une notification forgée. L'app 2.42 refuse ce qu'elle ne sait pas déchiffrer
pour les types que le serveur annonce ; chaque essai ici porte donc la valeur
qui ferait passer un serveur qui mentirait sur ce qu'il chiffre.

⚠️ Aucun essai ne pousse vraiment : ``requests.post`` est remplacé par un
espion, et la résolution DNS de l'endpoint par une réponse fixe (le banc n'a
pas de ``ntfy.example.test`` à résoudre).
"""
import base64
import json
import os
import struct
from unittest.mock import patch

import http_ece
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.bf_sms_archive.models import push_transport
from odoo.addons.bf_sms_archive.models.push_transport import (
    SmsUnifiedPush, parse_push_keys, push_request, webpush_encrypt,
)

BASE = "/bf_sms_archive/mobile/v1"


def _b64(octets):
    return base64.urlsafe_b64encode(octets).decode().rstrip("=")


def _unb64(texte):
    return base64.urlsafe_b64decode(texte + "=" * (-len(texte) % 4))


def abonnement():
    """Un abonnement comme l'app en fabrique un : (clé privée, p256dh, auth)."""
    prive = ec.generate_private_key(ec.SECP256R1())
    public = prive.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    return prive, _b64(public), _b64(os.urandom(16))


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.text = ""


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestWebpushChiffrement(TransactionCase):
    """Le chiffrement lui-même, sans ORM : la fonction pure du module."""

    def test_vecteur_de_l_annexe_a_de_la_rfc_8291(self):
        """Octet pour octet, le message chiffré de l'annexe A.

        Clé éphémère et sel injectés : c'est la seule façon de prouver que
        l'enchaînement (ECDH, HKDF avec `auth`, en-tête `aes128gcm`, taille
        d'enregistrement 4096, clé publique éphémère en `keyid`) est celui de
        la norme, et pas seulement un chiffrement que notre propre code sait
        relire.
        """
        prive_serveur = ec.derive_private_key(int.from_bytes(
            _unb64("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
            ec.SECP256R1())
        chiffre = webpush_encrypt(
            b"When I grow up, I want to be a watermelon",
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
            "BTBZMqHH6r4Tts7J_aSIgg",
            private_key=prive_serveur,
            salt=_unb64("DGv6ra1nlYgDCS1FRnbzlw"),
        )
        self.assertEqual(
            _b64(chiffre),
            "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlml"
            "MoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4M"
            "qgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")

    def test_le_vecteur_remis_a_l_app_se_relit(self):
        """Le vecteur que l'app éprouve de son côté (généré par http_ece) :
        même format que ce que le serveur émet."""
        prive = ec.derive_private_key(int.from_bytes(
            _unb64("JMvRX-NPp_TVcPHTwbgj8n1YQkSv50BWPQPjSTgc-VI"), "big"),
            ec.SECP256R1())
        clair = http_ece.decrypt(
            _unb64("IC2zepyfQB-DKd4euQcD6wAAEABBBJQXznORRGlOjkkI2rfu4LZ7En6b0LIiCkC"
                   "QdY_4LHLEOKUb3J6qLDY6k8uprPHIzaA2fIh7NRUI7XnF5DXqFwV8W_iHSYG5AC7"
                   "o2qS8lXdY3VxJnbBWvQx1gJxvbhKEekxzyB9KcM-sEWQful8VlRTFN9kqcFjU8N9"
                   "SdNJ7q5WhAJPdCFqmdzKcmEGaK-4vZV91mGEm8-4XroM4EuPoiZurCEXc6MxdIei"
                   "1IeZTE_6YmPHPcPq7x4Q"),
            private_key=prive, auth_secret=_unb64("KilcESeCYgpx6KaQ59yXJQ"),
            version="aes128gcm")
        self.assertEqual(json.loads(clair)["body"], "Bonjour é ✓")

    def test_la_poussee_chiffree_se_dechiffre_avec_la_cle_de_l_appareil(self):
        prive, p256dh, auth = abonnement()
        charge = {"type": "sms", "title": "Élise", "body": "Rendez-vous à 9 h ✓",
                  "thread_id": 42, "message_id": 7}
        corps, entetes = push_request(charge, p256dh, auth)
        self.assertEqual(entetes, {
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "aes128gcm",
            "TTL": "86400",
        })
        self.assertNotIn(b"Rendez-vous", corps, "Le texte du SMS est parti en clair.")
        # En-tête RFC 8188 : sel (16), rs (4), longueur du keyid (1), keyid.
        self.assertEqual(struct.unpack("!L", corps[16:20])[0], 4096)
        self.assertEqual(corps[20], 65)
        self.assertEqual(corps[21], 0x04, "Le keyid doit être la clé publique éphémère.")
        clair = http_ece.decrypt(corps, private_key=prive, auth_secret=_unb64(auth),
                                 version="aes128gcm")
        self.assertEqual(json.loads(clair.decode("utf-8")), charge)

    def test_une_cle_ephemere_et_un_sel_neufs_par_message(self):
        """Deux messages identiques ne doivent pas se ressembler : un sel ou
        une clé réutilisés rendraient le chiffrement AES-GCM cassable."""
        _prive, p256dh, auth = abonnement()
        un, _ = push_request({"type": "clear_all"}, p256dh, auth)
        deux, _ = push_request({"type": "clear_all"}, p256dh, auth)
        self.assertNotEqual(un[:16], deux[:16], "Sel réutilisé.")
        self.assertNotEqual(un[21:86], deux[21:86], "Clé éphémère réutilisée.")

    def test_sans_cles_le_clair_d_avant_a_l_octet_pres(self):
        """Une app ≤ 2.41.0 ne doit voir aucune différence."""
        charge = {"type": "clear", "thread_id": 3}
        self.assertEqual(push_request(charge),
                         (json.dumps(charge), {"Content-Type": "application/json"}))
        self.assertEqual(push_request(charge, None, None)[0], json.dumps(charge))

    def test_le_ttl_d_un_appel_est_court(self):
        _prive, p256dh, auth = abonnement()
        _corps, entetes = push_request({"type": "call"}, p256dh, auth,
                                       ttl=push_transport.TTL_APPEL)
        self.assertEqual(entetes["TTL"], "60")

    def test_un_message_trop_long_ne_part_pas_en_deux_enregistrements(self):
        _prive, p256dh, auth = abonnement()
        with self.assertRaises(ValueError):
            push_request({"type": "sms", "body": "x" * 5000}, p256dh, auth)

    def test_le_plus_long_message_tient_dans_ce_que_l_app_dechiffre(self):
        """⚠️ La borne est celle du déchiffreur de l'app, pas celle de la RFC :
        Tink refuse un message ENTIER de plus de 4096 octets. 3993 octets de
        clair doivent donner exactement 4096 octets ; un de plus doit lever
        ici plutôt que partir et être jeté sur le téléphone."""
        prive, p256dh, auth = abonnement()
        clair = b"x" * 3993
        corps = webpush_encrypt(clair, p256dh, auth)
        self.assertEqual(len(corps), 4096)
        self.assertEqual(http_ece.decrypt(corps, private_key=prive,
                                          auth_secret=_unb64(auth),
                                          version="aes128gcm"), clair)
        with self.assertRaises(ValueError):
            webpush_encrypt(clair + b"x", p256dh, auth)

    def test_cles_absentes_valides_et_invalides(self):
        _prive, p256dh, auth = abonnement()
        self.assertEqual(parse_push_keys(None, None), (False, False))
        self.assertEqual(parse_push_keys("", ""), (False, False))
        # Le remplissage est toléré à l'entrée et retiré au stockage.
        self.assertEqual(parse_push_keys(p256dh + "=", auth + "=="), (p256dh, auth))
        point_hors_courbe = _b64(b"\x04" + b"\x01" * 64)
        compresse = _b64(b"\x02" + _unb64(p256dh)[1:33])
        mauvais = {
            "une seule clé": (p256dh, None),
            "l'autre seule": (None, auth),
            "alphabet base64 standard": (p256dh[:-2] + "+/", auth),
            "pas une chaîne": (12345, auth),
            "point compressé": (compresse, auth),
            "point hors courbe": (point_hors_courbe, auth),
            "préfixe faux": (_b64(b"\x05" + _unb64(p256dh)[1:]), auth),
            "auth de 15 octets": (p256dh, _b64(os.urandom(15))),
            "auth de 32 octets": (p256dh, _b64(os.urandom(32))),
        }
        for cas, (cle, secret) in mauvais.items():
            with self.subTest(cas=cas), self.assertRaises(ValueError):
                parse_push_keys(cle, secret)


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestWebpushAppareil(TransactionCase):
    """Les clés sur l'appareil, et l'envoi qui s'en sert."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="sms_webpush_user",
            groups="base.group_user,bf_sms_archive.group_sms_user")
        cls.manager = new_test_user(
            cls.env, login="sms_webpush_manager",
            groups="base.group_user,bf_sms_archive.group_sms_manager")
        cls.Device = cls.env["sms.archive.mobile.device"]
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_sms_archive.push_enabled", "1")
        icp.set_param("bf_sms_archive.ntfy_publish_token", "tk-banc")
        icp.set_param("bf_sms_archive.ntfy_base_url", "https://ntfy.example.test")

    def _appareil(self, endpoint, cles=None):
        device = self.Device._issue(self.user.id, name="Banc webpush")
        vals = {"push_endpoint": endpoint}
        if cles:
            vals.update(push_p256dh=cles[1], push_auth=cles[2])
        device.sudo().write(vals)
        return device

    def test_les_types_chiffres_de_bf_sms_archive_seul(self):
        """⚠️ « call » n'appartient PAS à ce module : c'est bf_softphone qui
        l'ajoute. Appelée sur la définition de CE module, la liste ne doit pas
        l'annoncer, sinon un softphone d'avant le chiffrement verrait ses
        réveils en clair jetés par l'app."""
        self.assertEqual(
            SmsUnifiedPush._webpush_types(self.env["sms.archive.unifiedpush"]),
            ["sms", "clear", "clear_all", "genfox"])

    def test_effacer_ou_remplacer_l_endpoint_emporte_les_cles(self):
        sub = abonnement()
        device = self._appareil("https://ntfy.example.test/upA", sub)
        self.assertEqual(device.sudo().push_p256dh, sub[1])
        # Remplacé sans clés : les anciennes appartenaient à l'ancien abonnement.
        device.sudo().write({"push_endpoint": "https://ntfy.example.test/upB"})
        self.assertFalse(device.sudo().push_p256dh)
        self.assertFalse(device.sudo().push_auth)
        # Remplacé AVEC ses clés : elles restent.
        device.sudo().write({"push_endpoint": "https://ntfy.example.test/upC",
                             "push_p256dh": sub[1], "push_auth": sub[2]})
        self.assertEqual(device.sudo().push_auth, sub[2])
        # Effacé par un geste sans droit sur les clés (révocation par un
        # gestionnaire) : elles partent quand même.
        self.Device.with_user(self.manager).browse(device.id).write(
            {"push_endpoint": False})
        self.assertFalse(device.sudo().push_p256dh)

    def test_un_gestionnaire_ne_pose_pas_ses_cles_sur_l_appareil_d_autrui(self):
        """Poser ses propres clés ferait chiffrer les SMS d'autrui pour soi."""
        device = self._appareil("https://ntfy.example.test/upA")
        _prive, p256dh, auth = abonnement()
        with self.assertRaises(AccessError):
            self.Device.with_user(self.manager).browse(device.id).write(
                {"push_p256dh": p256dh, "push_auth": auth})

    def test_l_envoi_chiffre_pour_qui_a_ses_cles_et_seulement_pour_lui(self):
        """Deux téléphones de la même personne, deux versions de l'app."""
        sub = abonnement()
        self._appareil("https://ntfy.example.test/upNeuf", sub)
        self._appareil("https://ntfy.example.test/upVieux")
        envois = {}

        def espion(url, data=None, headers=None, **kw):
            envois[url] = (data, dict(headers), kw)
            return FakeResponse()

        charge = {"type": "sms", "title": "Banc", "body": "Code 4417",
                  "thread_id": 1, "message_id": 2}
        with patch.object(push_transport, "safe_push_endpoint", return_value=True), \
                patch.object(push_transport.requests, "post", side_effect=espion):
            self.env["sms.archive.unifiedpush"]._send(self.user, charge)

        corps, entetes, kw = envois["https://ntfy.example.test/upNeuf"]
        self.assertEqual(entetes["Content-Encoding"], "aes128gcm")
        self.assertEqual(entetes["Authorization"], "Bearer tk-banc")
        self.assertFalse(kw.get("allow_redirects"))
        self.assertNotIn(b"4417", corps)
        clair = http_ece.decrypt(corps, private_key=sub[0],
                                 auth_secret=_unb64(sub[2]), version="aes128gcm")
        self.assertEqual(json.loads(clair), charge)

        corps, entetes, kw = envois["https://ntfy.example.test/upVieux"]
        self.assertEqual(corps, json.dumps(charge))
        self.assertNotIn("Content-Encoding", entetes)
        self.assertEqual(entetes["Authorization"], "Bearer tk-banc")

    def test_le_jeton_ntfy_ne_suit_pas_un_endpoint_etranger_meme_chiffre(self):
        self._appareil("https://autre-distributeur.example/upX", abonnement())
        vus = []
        with patch.object(push_transport, "safe_push_endpoint", return_value=True), \
                patch.object(push_transport.requests, "post",
                             side_effect=lambda url, **kw: vus.append(kw["headers"])
                             or FakeResponse()):
            self.env["sms.archive.unifiedpush"]._send(self.user, {"type": "clear_all"})
        self.assertEqual(len(vus), 1)
        self.assertNotIn("Authorization", vus[0])
        self.assertEqual(vus[0]["Content-Encoding"], "aes128gcm")

    def test_un_chiffrement_impossible_ne_leve_pas_et_n_envoie_pas_de_clair(self):
        """Une clé abîmée en base : on perd la notification, jamais le cron, et
        surtout jamais de clair vers un appareil qui attend du chiffré."""
        device = self._appareil("https://ntfy.example.test/upA", abonnement())
        # ⚠️ Vider d'abord : `invalidate_recordset` pousse les écritures en
        # attente, et remettrait la bonne clé par-dessus la mauvaise.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE sms_archive_mobile_device SET push_p256dh = 'abîmée' WHERE id = %s",
            [device.id])
        device.invalidate_recordset()
        with patch.object(push_transport, "safe_push_endpoint", return_value=True), \
                patch.object(push_transport.requests, "post") as espion:
            self.env["sms.archive.unifiedpush"]._send(self.user, {"type": "clear_all"})
        espion.assert_not_called()


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestRegisterPushHttp(HttpCase):
    """``/register_push`` avec clés, sans clés, et avec des clés invalides."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="sms_regpush_user",
            groups="base.group_user,bf_sms_archive.group_sms_user")
        cls.device = cls.env["sms.archive.mobile.device"]._issue(
            cls.user.id, name="Banc register_push")
        cls.jeton = cls.device.device_token
        cls.env.cr.flush()

    def setUp(self):
        super().setUp()
        # Le banc n'a pas de DNS public : la garde anti-SSRF est éprouvée
        # ailleurs, ici elle répond oui.
        garde = patch("odoo.addons.bf_sms_archive.controllers.mobile_api._safe_media_url",
                      return_value=True)
        garde.start()
        self.addCleanup(garde.stop)

    def _inscrire(self, corps):
        return self.url_open(
            BASE + "/register_push", data=json.dumps(corps).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.jeton}, timeout=30)

    def test_avec_cles_le_serveur_annonce_ce_qu_il_chiffre(self):
        _prive, p256dh, auth = abonnement()
        reponse = self._inscrire({"endpoint": "https://ntfy.example.test/upA",
                                  "app_version": "2.42.0",
                                  "p256dh": p256dh + "=", "auth": auth})
        self.assertEqual(reponse.status_code, 200, reponse.text)
        corps = reponse.json()
        self.assertTrue(corps["ok"])
        self.assertTrue(corps["webpush"])
        self.assertEqual(corps["webpush_types"],
                         self.env["sms.archive.unifiedpush"]._webpush_types())
        for attendu in ("sms", "clear", "clear_all", "genfox"):
            self.assertIn(attendu, corps["webpush_types"])
        device = self.device.sudo()
        device.invalidate_recordset()
        self.assertEqual(device.push_p256dh, p256dh, "Stockée sans remplissage.")
        self.assertEqual(device.push_auth, auth)
        self.assertEqual(device.app_version, "2.42.0")

    def test_sans_cles_on_efface_et_on_pousse_en_clair(self):
        _prive, p256dh, auth = abonnement()
        self._inscrire({"endpoint": "https://ntfy.example.test/upA",
                        "p256dh": p256dh, "auth": auth})
        reponse = self._inscrire({"endpoint": "https://ntfy.example.test/upA",
                                  "app_version": "2.41.0"})
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json(), {"ok": True, "webpush": False,
                                          "webpush_types": []})
        device = self.device.sudo()
        device.invalidate_recordset()
        self.assertFalse(device.push_p256dh)
        self.assertFalse(device.push_auth)
        self.assertEqual(device.push_endpoint, "https://ntfy.example.test/upA")

    def test_la_deconnexion_efface_endpoint_et_cles(self):
        _prive, p256dh, auth = abonnement()
        self._inscrire({"endpoint": "https://ntfy.example.test/upA",
                        "p256dh": p256dh, "auth": auth})
        reponse = self.url_open(
            BASE + "/logout", data=b"{}",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.jeton}, timeout=30)
        self.assertEqual(reponse.status_code, 200)
        device = self.device.sudo().with_context(active_test=False)
        device.invalidate_recordset()
        self.assertFalse(device.active)
        self.assertFalse(device.push_endpoint)
        self.assertFalse(device.push_p256dh)
        self.assertFalse(device.push_auth)

    def test_des_cles_invalides_sont_un_400_et_ne_touchent_a_rien(self):
        _prive, p256dh, auth = abonnement()
        self._inscrire({"endpoint": "https://ntfy.example.test/upA",
                        "p256dh": p256dh, "auth": auth})
        for cles in ({"p256dh": p256dh}, {"auth": auth},
                     {"p256dh": _b64(b"\x04" + b"\x01" * 64), "auth": auth},
                     {"p256dh": p256dh, "auth": "court"}):
            with self.subTest(cles=cles):
                reponse = self._inscrire(
                    dict(cles, endpoint="https://ntfy.example.test/upAutre"))
                self.assertEqual(reponse.status_code, 400)
                self.assertEqual(reponse.json(), {"error": "invalid_push_keys"})
        device = self.device.sudo()
        device.invalidate_recordset()
        self.assertEqual(device.push_endpoint, "https://ntfy.example.test/upA")
        self.assertEqual(device.push_p256dh, p256dh)
