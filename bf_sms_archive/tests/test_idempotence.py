# -*- coding: utf-8 -*-
"""`POST /send` rejoué par la file hors ligne de l'app.

🔴 Un SMS rejoué repart chez une vraie personne. L'app envoie un
``client_uuid`` par message et le renvoie à chaque essai : le second essai
doit retrouver la première réponse, sans rappeler VOIP.ms.

⚠️ VOIP.ms est TOUJOURS remplacé par un double ici : aucun appel réel.
"""
import json
import uuid
from unittest.mock import patch

from odoo.tests import HttpCase, new_test_user, tagged

BASE = "/bf_sms_archive/mobile/v1"


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestMobileSendIdempotence(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = "base.group_user,bf_sms_archive.group_sms_user"
        cls.user = new_test_user(cls.env, login="sms_rejeu_t", groups=groupes)
        cls.autre = new_test_user(cls.env, login="sms_rejeu_autre_t", groups=groupes)
        cls.line = cls.env["sms.archive.line"].create({
            "label": "Ligne rejeu", "did": "5145550061",
            "owner_id": cls.user.id, "sms_enabled": True, "mms_enabled": False,
        })
        cls.line_autre = cls.env["sms.archive.line"].create({
            "label": "Ligne rejeu autre", "did": "5145550062",
            "owner_id": cls.autre.id, "sms_enabled": True, "mms_enabled": False,
        })
        Device = cls.env["sms.archive.mobile.device"]
        cls.jeton = Device._issue(cls.user.id, name="Banc rejeu").device_token
        cls.jeton_autre = Device._issue(cls.autre.id, name="Banc rejeu autre").device_token

    def setUp(self):
        super().setUp()
        self.envois = []

        def faux_envoi(voipms, did, dst, body):
            self.envois.append((did, dst, body))
            return "voipms-%d" % len(self.envois)

        rustine = patch.object(
            type(self.env["sms.archive.voipms"]), "_voipms_send_sms", faux_envoi)
        rustine.start()
        self.addCleanup(rustine.stop)
        # Et le MMS, par prudence : rien ne doit sortir d'un banc.
        rustine = patch.object(
            type(self.env["sms.archive.voipms"]), "_voipms_send_mms",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pas de MMS au banc")))
        rustine.start()
        self.addCleanup(rustine.stop)

    def _send(self, payload, jeton=None):
        return self.url_open(
            f"{BASE}/send", data=json.dumps(payload),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % (jeton or self.jeton)})

    def _messages(self, corps):
        return self.env["sms.archive.message"].sudo().search_count(
            [("body", "=", corps), ("direction", "=", "out")])

    def test_un_envoi_rejoue_ne_repart_pas(self):
        cle = str(uuid.uuid4())
        charge = {"phone": "+15145558861", "line_id": self.line.id,
                  "body": "Un seul envoi", "client_uuid": cle}
        a = self._send(charge)
        b = self._send(charge)
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.text)
        self.assertEqual(len(self.envois), 1, "le SMS serait reparti chez la personne")
        self.assertEqual(self._messages("Un seul envoi"), 1)
        self.assertEqual(b.json(), dict(a.json(), replay=True))

    def test_un_envoi_echoue_chez_voipms_pose_quand_meme_l_accuse(self):
        """Le message est CRÉÉ en « failed » : c'est un résultat, pas une panne."""
        cle = str(uuid.uuid4())
        appels = []

        def echec(voipms, did, dst, body):
            appels.append(body)
            raise RuntimeError("VOIP.ms a refusé")

        charge = {"phone": "+15145558862", "line_id": self.line.id,
                  "body": "Envoi echoue", "client_uuid": cle}
        with patch.object(type(self.env["sms.archive.voipms"]), "_voipms_send_sms", echec):
            a = self._send(charge)
            b = self._send(charge)
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual(a.json()["message"]["delivery_state"], "failed")
        self.assertEqual(len(appels), 1)
        self.assertEqual(self._messages("Envoi echoue"), 1)
        self.assertTrue(b.json()["replay"])
        self.assertEqual(b.json()["message"]["delivery_state"], "failed")

    def test_une_erreur_ne_pose_pas_d_accuse(self):
        cle = str(uuid.uuid4())
        refus = self._send({"line_id": self.line.id, "body": "Sans destinataire",
                            "client_uuid": cle})
        self.assertEqual(refus.status_code, 400)
        self.assertFalse(self.env["sms.archive.mobile.receipt"].sudo().search_count(
            [("client_uuid", "=", cle)]))
        vide = self._send({"phone": "+15145558863", "line_id": self.line.id,
                           "body": "  ", "client_uuid": cle})
        self.assertEqual(vide.status_code, 400)
        self.assertEqual(self.envois, [])

    def test_sans_client_uuid_rien_ne_change(self):
        charge = {"phone": "+15145558864", "line_id": self.line.id, "body": "Deux fois"}
        a = self._send(charge)
        b = self._send(charge)
        self.assertEqual((a.status_code, b.status_code), (200, 200))
        self.assertEqual(len(self.envois), 2)
        self.assertNotIn("replay", b.json())

    def test_uuid_invalide_est_un_400_sans_envoi(self):
        r = self._send({"phone": "+15145558865", "line_id": self.line.id,
                        "body": "Invalide", "client_uuid": "not-a-uuid"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_client_uuid")
        r = self._send({"phone": "+15145558865", "line_id": self.line.id,
                        "body": "Invalide", "client_uuid": 12})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.envois, [])

    def test_le_uuid_d_un_autre_usager_ne_rend_pas_sa_reponse(self):
        cle = str(uuid.uuid4())
        a = self._send({"phone": "+15145558866", "line_id": self.line.id,
                        "body": "Cloison", "client_uuid": cle})
        b = self._send({"phone": "+15145558866", "line_id": self.line_autre.id,
                        "body": "Cloison", "client_uuid": cle}, jeton=self.jeton_autre)
        self.assertEqual(b.status_code, 200, b.text)
        self.assertNotIn("replay", b.json())
        self.assertNotEqual(a.json()["message"]["id"], b.json()["message"]["id"])
        self.assertEqual(len(self.envois), 2)

    def test_nouvelle_conversation_puis_reponse_dans_le_fil(self):
        """Les deux formes de /send (``phone`` et ``thread_id``) sont gardées."""
        a = self._send({"phone": "+15145558867", "line_id": self.line.id,
                        "body": "Bonjour", "client_uuid": str(uuid.uuid4())})
        fil = a.json()["thread_id"]
        cle = str(uuid.uuid4())
        b = self._send({"thread_id": fil, "body": "Suite", "client_uuid": cle})
        c = self._send({"thread_id": fil, "body": "Suite", "client_uuid": cle})
        self.assertEqual(b.status_code, 200, b.text)
        self.assertTrue(c.json()["replay"])
        self.assertEqual(len(self.envois), 2)
