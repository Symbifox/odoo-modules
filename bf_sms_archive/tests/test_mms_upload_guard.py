# -*- coding: utf-8 -*-
"""Garde-fou des pièces jointes MMS envoyées par l'app.

``/send`` accepte désormais des médias. La validation vit dans le contrôleur
parce que c'est le seul endroit qui voit ce que le téléphone a réellement
posté : ``action_send`` reçoit ensuite une liste déjà propre.

⚠️ Ce qui se paierait cher sans ce garde-fou : VOIP.ms n'expose que
``media1``..``media3`` sur ``sendMMS``. Une quatrième pièce partirait dans le
vide **sans erreur** — le message serait créé, marqué envoyé, et une photo
manquerait chez le destinataire sans que rien ne le dise.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..controllers.mobile_api import (
    MMS_MAX_PART_BYTES,
    MMS_MAX_PARTS,
    _parse_media,
)

import base64


def _part(size=10, content_type="image/jpeg", filename="photo.jpg"):
    return {
        "filename": filename,
        "content_type": content_type,
        "data_b64": base64.b64encode(b"x" * size).decode(),
    }


@tagged("post_install", "-at_install")
class TestMmsUploadGuard(TransactionCase):

    def test_absent_media_stays_a_plain_sms(self):
        """Rien de joint = liste vide, et ``action_send`` repart en SMS."""
        for empty in (None, False, [], ""):
            self.assertEqual(_parse_media(empty), [])

    def test_three_parts_pass_four_are_refused(self):
        self.assertEqual(len(_parse_media([_part()] * MMS_MAX_PARTS)),
                         MMS_MAX_PARTS)
        with self.assertRaises(UserError):
            _parse_media([_part()] * (MMS_MAX_PARTS + 1))

    def test_oversized_part_is_refused_before_voipms_sees_it(self):
        with self.assertRaises(UserError):
            _parse_media([_part(size=MMS_MAX_PART_BYTES + 1)])

    def test_total_size_is_capped_even_when_each_part_fits(self):
        big = _part(size=MMS_MAX_PART_BYTES)
        with self.assertRaises(UserError):
            _parse_media([big] * MMS_MAX_PARTS)

    def test_unreadable_base64_is_refused(self):
        with self.assertRaises(UserError):
            _parse_media([{"filename": "x", "content_type": "image/png",
                           "data_b64": "pas du base64 !!"}])

    def test_empty_part_is_refused(self):
        with self.assertRaises(UserError):
            _parse_media([{"filename": "x", "content_type": "image/png",
                           "data_b64": ""}])

    def test_a_bogus_mime_falls_back_rather_than_reaching_the_data_uri(self):
        """Le type finit dans un ``data:<type>;base64,…`` : il doit être sain."""
        parsed = _parse_media([_part(content_type="image/png; rm -rf")])
        self.assertEqual(parsed[0]["content_type"], "application/octet-stream")

    def test_filename_cannot_carry_a_path(self):
        parsed = _parse_media([_part(filename="../../etc/passwd")])
        self.assertNotIn("/", parsed[0]["filename"])

    def test_a_list_is_required(self):
        with self.assertRaises(UserError):
            _parse_media({"filename": "x"})
