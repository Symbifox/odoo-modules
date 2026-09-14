"""La présence à une rencontre : la mienne, celle en cours, pas une autre."""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestRencontre(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.invitee = new_test_user(cls.env, login="rencontre-invitee", groups="base.group_user")
        cls.intrus = new_test_user(cls.env, login="rencontre-intrus", groups="base.group_user")
        cls.rencontre = cls.env["meeting.record"].create({
            "name": "Comité de suivi", "date": fields.Datetime.now() - timedelta(minutes=5),
            "duration_minutes": 60, "invited_ids": [(6, 0, [cls.invitee.partner_id.id])],
        })
        cls.table = cls.env["bf.nfc.tag"].create({
            "name": "Table de la salle",
            "gesture_id": cls.env.ref("bf_nfc_meeting.gesture_meeting_presence").id,
        })

    def _presence(self, personne, rencontre=None):
        return self.env["meeting.attendance"].search([
            ("meeting_id", "=", (rencontre or self.rencontre).id), ("partner_id", "=", personne.partner_id.id)])

    def test_l_invitee_est_notee_presente(self):
        r = self.table.with_user(self.invitee).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self._presence(self.invitee).status, "present")
        r = self.table.with_user(self.invitee).taper("app")
        self.assertIn("déjà notée", r["message"])

    def test_sans_invitation_la_table_ne_devine_pas(self):
        r = self.table.with_user(self.intrus).taper("app")
        self.assertEqual(r["statut"], "refused")
        self.assertFalse(self._presence(self.intrus))

    def test_la_pastille_de_la_rencontre_note_meme_sans_invitation(self):
        pastille = self.env["bf.nfc.tag"].create({
            "name": "Comité", "gesture_id": self.env.ref("bf_nfc_meeting.gesture_meeting_presence").id,
            "res_model": "meeting.record", "res_id": self.rencontre.id,
        })
        r = pastille.with_user(self.intrus).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self._presence(self.intrus).status, "present")

    def test_excuse_puis_arrive_devient_present(self):
        self.env["meeting.attendance"].create({
            "meeting_id": self.rencontre.id, "partner_id": self.invitee.partner_id.id, "status": "excused"})
        self.table.with_user(self.invitee).taper("app")
        self.assertEqual(self._presence(self.invitee).status, "present")

    def test_deux_rencontres_une_question(self):
        autre = self.env["meeting.record"].create({
            "name": "Point rapide", "date": fields.Datetime.now(),
            "duration_minutes": 30, "invited_ids": [(6, 0, [self.invitee.partner_id.id])],
        })
        q = self.table.with_user(self.invitee).taper("app")
        self.assertEqual(q["statut"], "choice")
        r = self.table.with_user(self.invitee).taper("app", choix=str(autre.id))
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self._presence(self.invitee, autre).status, "present")
        self.assertFalse(self._presence(self.invitee))

    def test_la_pastille_signee_ne_note_pas(self):
        r = self.table.with_user(self.invitee).taper("signed")
        self.assertEqual(r["statut"], "refused")
