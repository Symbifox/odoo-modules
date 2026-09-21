"""Poussées chiffrées en WebPush (RFC 8291), audit du 2026-09-08 (C-M3).

L'objet et l'aperçu de chaque courriel traversaient ntfy en clair, et
quiconque connaissait l'endpoint d'un appareil pouvait lui poster une
notification forgée. L'app 2.42 refuse ce qu'elle ne sait pas déchiffrer pour
les types que le serveur annonce : chaque essai porte la valeur qui ferait
passer un serveur qui mentirait sur ce qu'il chiffre.

⚠️ Aucun essai ne pousse vraiment : ``requests.post`` est un espion, et la
résolution DNS de l'endpoint répond oui (le banc n'a pas de DNS public).
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
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.bf_email_management.models import push_transport
from odoo.addons.bf_email_management.models.push_transport import (
    parse_push_keys, push_request, webpush_encrypt,
)

BASE = "/bf_email_management/mobile/v1"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def subscription():
    """A subscription the way the app makes one: (private key, p256dh, auth)."""
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    return private, _b64(public), _b64(os.urandom(16))


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.text = ""


@tagged("post_install", "-at_install")
class TestWebpushEncryption(TransactionCase):

    def test_rfc_8291_appendix_a_vector(self):
        """Byte for byte, the RFC's encrypted message: the chain (ECDH, HKDF
        with `auth`, `aes128gcm` header, rs 4096, ephemeral key as `keyid`) is
        the standard's, not merely something our own code can read back."""
        server_key = ec.derive_private_key(int.from_bytes(
            _unb64("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
            ec.SECP256R1())
        encrypted = webpush_encrypt(
            b"When I grow up, I want to be a watermelon",
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
            "BTBZMqHH6r4Tts7J_aSIgg",
            private_key=server_key,
            salt=_unb64("DGv6ra1nlYgDCS1FRnbzlw"),
        )
        self.assertEqual(
            _b64(encrypted),
            "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlml"
            "MoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4M"
            "qgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")

    def test_an_encrypted_push_decrypts_with_the_device_key(self):
        private, p256dh, auth = subscription()
        payload = {"type": "mail", "title": "Élise", "body": "Soumission révisée",
                   "preview": "Voici le prix ✓", "email_id": 9,
                   "thread_key": "<x@y>", "account_id": 1}
        body, headers = push_request(payload, p256dh, auth)
        self.assertEqual(headers, {"Content-Type": "application/octet-stream",
                                   "Content-Encoding": "aes128gcm", "TTL": "86400"})
        self.assertNotIn("Soumission".encode(), body)
        self.assertEqual(struct.unpack("!L", body[16:20])[0], 4096)
        self.assertEqual((body[20], body[21]), (65, 0x04))
        plain = http_ece.decrypt(body, private_key=private,
                                 auth_secret=_unb64(auth), version="aes128gcm")
        self.assertEqual(json.loads(plain.decode("utf-8")), payload)

    def test_fresh_salt_and_ephemeral_key_per_message(self):
        _private, p256dh, auth = subscription()
        one, _ = push_request({"type": "mail_clear_all"}, p256dh, auth)
        two, _ = push_request({"type": "mail_clear_all"}, p256dh, auth)
        self.assertNotEqual(one[:16], two[:16])
        self.assertNotEqual(one[21:86], two[21:86])

    def test_the_longest_message_fits_what_the_app_decrypts(self):
        """⚠️ The bound is the app decrypter's, not the RFC's: Tink refuses a
        WHOLE message past 4096 bytes. 3993 bytes of plaintext must give
        exactly 4096 bytes; one more must raise here rather than leave and be
        dropped on the phone."""
        private, p256dh, auth = subscription()
        plaintext = b"x" * 3993
        body = webpush_encrypt(plaintext, p256dh, auth)
        self.assertEqual(len(body), 4096)
        self.assertEqual(http_ece.decrypt(body, private_key=private,
                                          auth_secret=_unb64(auth),
                                          version="aes128gcm"), plaintext)
        with self.assertRaises(ValueError):
            webpush_encrypt(plaintext + b"x", p256dh, auth)

    def test_without_keys_the_old_clear_json_byte_for_byte(self):
        payload = {"type": "mail_clear", "email_id": 3}
        self.assertEqual(push_request(payload),
                         (json.dumps(payload), {"Content-Type": "application/json"}))

    def test_key_parsing(self):
        _private, p256dh, auth = subscription()
        self.assertEqual(parse_push_keys(None, None), (False, False))
        self.assertEqual(parse_push_keys(p256dh + "=", auth), (p256dh, auth))
        bad = {
            "p256dh alone": (p256dh, None),
            "auth alone": (None, auth),
            "standard base64 alphabet": (p256dh[:-2] + "+/", auth),
            "off the curve": (_b64(b"\x04" + b"\x01" * 64), auth),
            "compressed point": (_b64(b"\x03" + _unb64(p256dh)[1:33]), auth),
            "auth 15 bytes": (p256dh, _b64(os.urandom(15))),
        }
        for case, (key, secret) in bad.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                parse_push_keys(key, secret)


@tagged("post_install", "-at_install")
class TestWebpushDevice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.owner = Users.create({
            "name": "Porteur Webpush", "login": "mobile.webpush@test.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.admin_mail = Users.create({
            "name": "Admin Courriel", "login": "mobile.webpush.admin@test.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("bf_email_management.group_email_admin").id])],
        })
        cls.Device = cls.env["bf.email.mobile.device"]
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_email.push_enabled", "1")
        icp.set_param("bf_email_management.ntfy_publish_token", "tk-courriel")
        icp.set_param("bf_sms_archive.ntfy_base_url", "https://ntfy.example.test")

    def _device(self, endpoint, sub=None):
        device = self.Device._issue(self.owner.id, name="Banc webpush")
        vals = {"push_endpoint": endpoint}
        if sub:
            vals.update(push_p256dh=sub[1], push_auth=sub[2])
        device.sudo().write(vals)
        return device

    def test_the_types_this_server_encrypts(self):
        # "wake" is the offboarding killswitch's nudge (offboarding): it goes
        # out through ``_envoyer_a`` like the others, so it belongs here.
        self.assertEqual(self.env["bf.email.unifiedpush"]._webpush_types(),
                         ["mail", "mail_clear", "mail_clear_all", "wake"])

    def test_clearing_or_replacing_the_endpoint_takes_the_keys(self):
        sub = subscription()
        device = self._device("https://ntfy.example.test/upA", sub)
        device.sudo().write({"push_endpoint": "https://ntfy.example.test/upB"})
        self.assertFalse(device.sudo().push_p256dh)
        device.sudo().write({"push_endpoint": "https://ntfy.example.test/upC",
                             "push_p256dh": sub[1], "push_auth": sub[2]})
        self.assertEqual(device.sudo().push_auth, sub[2])
        # Cleared by a dead endpoint, the way `_send` does it.
        with patch.object(push_transport, "safe_push_endpoint", return_value=True), \
                patch.object(push_transport.requests, "post",
                             return_value=FakeResponse(410)):
            self.env["bf.email.unifiedpush"]._send(self.owner, {"type": "mail_clear_all"})
        self.assertFalse(device.sudo().push_endpoint)
        self.assertFalse(device.sudo().push_p256dh)
        self.assertFalse(device.sudo().push_auth)

    def test_nobody_plants_keys_on_a_device_outside_sudo(self):
        """Keys only come from ``/register_push``, in sudo.

        ⚠️ Tried on the email admin's OWN device: someone else's is already
        closed by the owner record rule, and a test on it would pass without
        the field guard ever running (measured by mutation, 2026-09-14)."""
        _private, p256dh, auth = subscription()
        own = self.Device._issue(self.admin_mail.id, name="Banc admin")
        own.sudo().write({"push_endpoint": "https://ntfy.example.test/upAdmin"})
        with self.assertRaises(AccessError):
            self.Device.with_user(self.admin_mail).browse(own.id).write(
                {"push_p256dh": p256dh, "push_auth": auth})
        # Clearing the endpoint stays an ordinary gesture, and takes the keys.
        own.sudo().write({"push_p256dh": p256dh, "push_auth": auth})
        self.Device.with_user(self.admin_mail).browse(own.id).write(
            {"push_endpoint": False})
        self.assertFalse(own.sudo().push_p256dh)

    def test_send_encrypts_for_the_device_that_has_keys_only(self):
        sub = subscription()
        self._device("https://ntfy.example.test/upNew", sub)
        self._device("https://ntfy.example.test/upOld")
        sent = {}

        def spy(url, data=None, headers=None, **kw):
            sent[url] = (data, dict(headers), kw)
            return FakeResponse()

        payload = {"type": "mail", "title": "Banc", "body": "Contrat signé",
                   "preview": "", "email_id": 1, "thread_key": "id:1",
                   "account_id": False}
        with patch.object(push_transport, "safe_push_endpoint", return_value=True), \
                patch.object(push_transport.requests, "post", side_effect=spy):
            self.env["bf.email.unifiedpush"]._send(self.owner, payload)

        body, headers, kw = sent["https://ntfy.example.test/upNew"]
        self.assertEqual(headers["Content-Encoding"], "aes128gcm")
        self.assertEqual(headers["Authorization"], "Bearer tk-courriel")
        self.assertIs(kw["allow_redirects"], False)
        self.assertNotIn("Contrat".encode(), body)
        plain = http_ece.decrypt(body, private_key=sub[0],
                                 auth_secret=_unb64(sub[2]), version="aes128gcm")
        self.assertEqual(json.loads(plain), payload)

        body, headers, _kw = sent["https://ntfy.example.test/upOld"]
        self.assertEqual(body, json.dumps(payload))
        self.assertNotIn("Content-Encoding", headers)

    def test_the_token_still_stays_home_when_encrypted(self):
        self._device("https://autre.example/upX", subscription())
        seen = []
        with patch.object(push_transport, "safe_push_endpoint", return_value=True), \
                patch.object(push_transport.requests, "post",
                             side_effect=lambda url, **kw: seen.append(kw["headers"])
                             or FakeResponse()):
            self.env["bf.email.unifiedpush"]._send(self.owner, {"type": "mail_clear_all"})
        self.assertEqual(len(seen), 1)
        self.assertNotIn("Authorization", seen[0])


@tagged("post_install", "-at_install")
class TestRegisterPushHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Porteur Inscription", "login": "mobile.regpush@test.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.device = cls.env["bf.email.mobile.device"]._issue(
            cls.owner.id, name="Banc inscription")
        cls.token = cls.device.device_token
        cls.env.cr.flush()

    def setUp(self):
        super().setUp()
        guard = patch(
            "odoo.addons.bf_email_management.controllers.mobile_api.safe_push_endpoint",
            return_value=True)
        guard.start()
        self.addCleanup(guard.stop)

    def _register(self, body):
        return self.url_open(
            BASE + "/register_push", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.token}, timeout=30)

    def test_with_keys(self):
        _private, p256dh, auth = subscription()
        response = self._register({"endpoint": "https://ntfy.example.test/upA",
                                   "app_version": "2.42.0",
                                   "p256dh": p256dh, "auth": auth})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "ok": True, "webpush": True,
            "webpush_types": ["mail", "mail_clear", "mail_clear_all", "wake"]})
        device = self.device.sudo()
        device.invalidate_recordset()
        self.assertEqual((device.push_p256dh, device.push_auth), (p256dh, auth))

    def test_without_keys_clears_them(self):
        _private, p256dh, auth = subscription()
        self._register({"endpoint": "https://ntfy.example.test/upA",
                        "p256dh": p256dh, "auth": auth})
        response = self._register({"endpoint": "https://ntfy.example.test/upA"})
        self.assertEqual(response.json(), {"ok": True, "webpush": False,
                                           "webpush_types": []})
        device = self.device.sudo()
        device.invalidate_recordset()
        self.assertFalse(device.push_p256dh)
        self.assertFalse(device.push_auth)

    def test_invalid_keys_are_a_400_and_change_nothing(self):
        _private, p256dh, auth = subscription()
        self._register({"endpoint": "https://ntfy.example.test/upA",
                        "p256dh": p256dh, "auth": auth})
        for keys in ({"auth": auth}, {"p256dh": p256dh, "auth": "AAAA"},
                     {"p256dh": "pas de la base64 !", "auth": auth}):
            with self.subTest(keys=keys):
                response = self._register(
                    dict(keys, endpoint="https://ntfy.example.test/upOther"))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"error": "invalid_push_keys"})
        device = self.device.sudo()
        device.invalidate_recordset()
        self.assertEqual(device.push_endpoint, "https://ntfy.example.test/upA")
        self.assertEqual(device.push_p256dh, p256dh)

    def test_logout_clears_endpoint_and_keys(self):
        _private, p256dh, auth = subscription()
        self._register({"endpoint": "https://ntfy.example.test/upA",
                        "p256dh": p256dh, "auth": auth})
        response = self.url_open(
            BASE + "/logout", data=b"{}",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.token}, timeout=30)
        self.assertEqual(response.status_code, 200)
        device = self.device.sudo().with_context(active_test=False)
        device.invalidate_recordset()
        self.assertFalse(device.push_endpoint)
        self.assertFalse(device.push_p256dh)
