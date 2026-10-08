"""Le rejeu de la file hors ligne de Symbifox Mobile.

Une photo envoyée dont la réponse s'est perdue revient avec le MÊME
``client_uuid`` : elle doit retrouver sa première réponse, pas créer une
seconde facture brouillon ou une seconde note.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from .test_scan import HEIC, JPEG

BASE = "/bf_scan/mobile/v1"


@tagged("post_install", "-at_install")
class EssaiScanIdempotence(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        interne = cls.env.ref("base.group_user")
        cls.luc = Users.create({
            "name": "Luc Rejoue", "login": "luc_rejoue",
            "email": "luc.rejoue@essai.test",
            "groups_id": [(6, 0, [interne.id, cls.env.ref("account.group_account_invoice").id])],
        })
        cls.mia = Users.create({
            "name": "Mia Rejoue", "login": "mia_rejoue",
            "email": "mia.rejoue@essai.test",
            "groups_id": [(6, 0, [interne.id])],
        })

    def _envoyer(self, route, usager, cle=None, octets=JPEG, nom="photo.jpg", **champs):
        if cle is not None:
            champs["client_uuid"] = cle
        with patch("odoo.addons.bf_scan.controllers.mobile_api._device",
                   lambda: SimpleNamespace(user_id=usager, _fields={})):
            return self.url_open(f"{BASE}/{route}", data=champs,
                                 files={"image": (nom, octets, "image/jpeg")}, timeout=120)

    def _notes(self, titre):
        return self.env["bf.note"].sudo().search_count([("name", "=", titre)])

    def test_document_rejoue_ne_cree_qu_une_note(self):
        cle = str(uuid.uuid4())
        a = self._envoyer("document", self.mia, cle, titre="Reçu rejoué")
        b = self._envoyer("document", self.mia, cle, titre="Reçu rejoué")
        self.assertEqual((a.status_code, b.status_code), (200, 200), b.text)
        self.assertEqual(self._notes("Reçu rejoué"), 1)
        self.assertEqual(b.json(), dict(a.json(), replay=True))

    def test_facture_rejouee_ne_cree_qu_un_brouillon(self):
        Move = self.env["account.move"].sudo()
        avant = Move.search_count([("move_type", "=", "in_invoice")])
        cle = str(uuid.uuid4())
        a = self._envoyer("facture", self.luc, cle)
        b = self._envoyer("facture", self.luc, cle)
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.text)
        self.assertEqual(Move.search_count([("move_type", "=", "in_invoice")]), avant + 1)
        self.assertEqual(b.json()["move_id"], a.json()["move_id"])
        self.assertTrue(b.json()["replay"])

    def test_sans_client_uuid_rien_ne_change(self):
        self._envoyer("document", self.mia, titre="Reçu sans garde")
        b = self._envoyer("document", self.mia, titre="Reçu sans garde")
        self.assertEqual(self._notes("Reçu sans garde"), 2)
        self.assertNotIn("replay", b.json())

    def test_uuid_invalide_est_un_400(self):
        r = self._envoyer("document", self.mia, "zzz", titre="Reçu invalide")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_client_uuid")
        self.assertEqual(self._notes("Reçu invalide"), 0)

    def test_le_uuid_d_un_autre_usager_ne_rend_pas_sa_reponse(self):
        cle = str(uuid.uuid4())
        a = self._envoyer("document", self.mia, cle, titre="Reçu partagé")
        b = self._envoyer("document", self.luc, cle, titre="Reçu partagé")
        self.assertEqual(b.status_code, 200, b.text)
        self.assertNotIn("replay", b.json())
        self.assertNotEqual(a.json()["note_id"], b.json()["note_id"])

    def test_un_refus_ne_pose_pas_d_accuse(self):
        cle = str(uuid.uuid4())
        refus = self._envoyer("document", self.mia, cle, octets=HEIC, nom="IMG.HEIC",
                              titre="Reçu HEIC")
        self.assertEqual(refus.status_code, 400)
        self.assertFalse(self.env["bf.scan.mobile.receipt"].search_count(
            [("client_uuid", "=", cle)]))
        reprise = self._envoyer("document", self.mia, cle, titre="Reçu HEIC")
        self.assertEqual(reprise.status_code, 200, reprise.text)
        self.assertNotIn("replay", reprise.json())
