# -*- coding: utf-8 -*-
"""`POST /send` avec pièces jointes, de bout en bout.

Le garde-fou est éprouvé à part (`test_mms_upload_guard`). Ce qui se joue ici
est le **câblage** : que le média posté par le téléphone arrive jusqu'à
``action_send``, en ressorte en MMS, et que les pièces soient archivées sur le
message. C'est la seule ligne du contrôleur qui ne se voit pas autrement, et
c'est celle dont l'échec est le plus silencieux — un serveur qui ignore
``media`` envoie un SMS texte, répond « ok », et la photo n'arrive jamais sans
que rien ne le dise.

⚠️ L'appel à VOIP.ms échoue forcément ici (aucun identifiant sur un banc), et
c'est voulu : ``action_send`` journalise l'échec et crée quand même le message.
On ne teste donc pas la livraison — on teste que le message porte ``is_mms`` et
ses pièces, ce qui prouve que le chemin MMS a bien été pris.
"""
import base64
import json

from odoo.tests import HttpCase, new_test_user, tagged

# Un PNG 1×1 valide : assez pour être décodé, trop petit pour être redimensionné.
PNG_1PX = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)).decode()

BASE = "/bf_sms_archive/mobile/v1"


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestMobileSendMedia(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="sms_mms_sender",
            groups="bf_sms_archive.group_sms_user",
        )
        cls.line = cls.env["sms.archive.line"].create({
            "label": "Ligne MMS",
            "did": "5145550009",
            "owner_id": cls.user.id,
            "sms_enabled": True,
            "mms_enabled": True,
        })
        cls.sms_only = cls.env["sms.archive.line"].create({
            "label": "Ligne sans MMS",
            "did": "5145550010",
            "owner_id": cls.user.id,
            "sms_enabled": True,
            "mms_enabled": False,
        })
        cls.device = cls.env["sms.archive.mobile.device"]._issue(
            cls.user.id, name="Banc MMS")

    def _send(self, payload):
        return self.url_open(
            f"{BASE}/send",
            data=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.device.device_token,
            },
        )

    def _message(self, response):
        body = response.json()
        self.assertTrue(body.get("ok"), body)
        return self.env["sms.archive.message"].browse(body["message"]["id"])

    def test_a_shared_photo_leaves_as_an_mms_with_its_parts(self):
        response = self._send({
            "phone": "+15145558801",
            "line_id": self.line.id,
            "body": "Voici la photo",
            "media": [{
                "filename": "photo.png",
                "content_type": "image/png",
                "data_b64": PNG_1PX,
            }],
        })
        self.assertEqual(response.status_code, 200, response.text)
        message = self._message(response)
        self.assertTrue(message.is_mms, "le chemin MMS n'a pas été pris")
        self.assertEqual(len(message.mms_part_ids), 1)
        self.assertEqual(message.mms_part_ids.filename, "photo.png")
        self.assertEqual(message.body, "Voici la photo")

    def test_a_photo_without_a_word_is_a_whole_message(self):
        response = self._send({
            "phone": "+15145558802",
            "line_id": self.line.id,
            "body": "",
            "media": [{"filename": "p.png", "content_type": "image/png",
                       "data_b64": PNG_1PX}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self._message(response).is_mms)

    def test_nothing_at_all_is_refused(self):
        """Avant, un corps vide partait quand même — un SMS blanc, facturé."""
        response = self._send({
            "phone": "+15145558803", "line_id": self.line.id, "body": "   ",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "empty_message")

    def test_a_line_without_mms_says_so_rather_than_sending_the_text_alone(self):
        """⚠️ ``action_send`` pose bien la question, mais dans son bloc `try`.

        L'erreur y est rattrapée, le message créé quand même en « échoué », et
        la route répondrait « ok » : l'app enchaînerait sur le fil, où la photo
        aurait l'air partie. D'où un contrôle dans le contrôleur, AVANT qu'il y
        ait un message à défaire.
        """
        before = self.env["sms.archive.message"].search_count([])
        response = self._send({
            "phone": "+15145558804",
            "line_id": self.sms_only.id,
            "body": "Avec photo",
            "media": [{"filename": "p.png", "content_type": "image/png",
                       "data_b64": PNG_1PX}],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("MMS", response.json().get("error", ""))
        self.assertEqual(self.env["sms.archive.message"].search_count([]), before,
                         "aucun message ne doit rester derrière un refus")

    def test_an_oversized_attachment_never_reaches_the_message(self):
        before = self.env["sms.archive.message"].search_count([])
        response = self._send({
            "phone": "+15145558805",
            "line_id": self.line.id,
            "body": "Trop gros",
            "media": [{
                "filename": "gros.bin",
                "content_type": "application/octet-stream",
                "data_b64": base64.b64encode(b"x" * 1_000_001).decode(),
            }],
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.env["sms.archive.message"].search_count([]), before,
                         "aucun message ne doit être créé sur un refus")

    def test_a_plain_text_send_still_behaves_as_before(self):
        response = self._send({
            "phone": "+15145558806", "line_id": self.line.id, "body": "Bonjour",
        })
        self.assertEqual(response.status_code, 200, response.text)
        message = self._message(response)
        self.assertFalse(message.mms_part_ids)
