"""La présence par pastille : la bonne séance, la bonne personne, la bonne heure."""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPresence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.eleve = new_test_user(cls.env, login="presence-eleve", groups="base.group_user")
        cls.salle = cls.env["res.partner"].create({"name": "Salle de formation A"})
        maintenant = fields.Datetime.now()
        cls.seance = cls.env["event.event"].create({
            "name": "Loi 25 : l'essentiel", "address_id": cls.salle.id,
            "date_begin": maintenant - timedelta(minutes=10),
            "date_end": maintenant + timedelta(hours=2),
        })
        cls.porte = cls.env["bf.nfc.tag"].create({
            "name": "Porte de la salle A",
            "gesture_id": cls.env.ref("bf_nfc_event.gesture_event_presence").id,
            "res_model": "res.partner", "res_id": cls.salle.id,
        })

    def _inscription(self, seance=None):
        return self.env["event.registration"].search([
            ("event_id", "=", (seance or self.seance).id), ("partner_id", "=", self.eleve.partner_id.id)])

    def test_la_porte_note_la_presence_et_cree_l_inscription(self):
        r = self.porte.with_user(self.eleve).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self._inscription().state, "done")
        r = self.porte.with_user(self.eleve).taper("app")
        self.assertIn("déjà notée", r["message"])
        self.assertEqual(len(self._inscription()), 1)

    def test_une_inscription_existante_passe_a_present(self):
        self.env["event.registration"].create({
            "event_id": self.seance.id, "partner_id": self.eleve.partner_id.id})
        r = self.porte.with_user(self.eleve).taper("app")
        self.assertIn("Bonne séance", r["message"])
        self.assertEqual(self._inscription().state, "done")

    def test_deux_seances_au_meme_endroit_une_question(self):
        autre = self.env["event.event"].create({
            "name": "Atelier parallèle", "address_id": self.salle.id,
            "date_begin": fields.Datetime.now() - timedelta(minutes=5),
            "date_end": fields.Datetime.now() + timedelta(hours=1),
        })
        q = self.porte.with_user(self.eleve).taper("app")
        self.assertEqual(q["statut"], "choice")
        self.assertEqual(len(q["choix"]), 2)
        self.assertFalse(self._inscription())
        r = self.porte.with_user(self.eleve).taper("app", choix=str(autre.id))
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self._inscription(autre).state, "done")
        self.assertFalse(self._inscription())

    def test_hors_seance_refuse(self):
        demain = self.env["event.event"].create({
            "name": "Séance de demain",
            "date_begin": fields.Datetime.now() + timedelta(days=1),
            "date_end": fields.Datetime.now() + timedelta(days=1, hours=2),
        })
        pastille = self.env["bf.nfc.tag"].create({
            "name": "Demain", "gesture_id": self.env.ref("bf_nfc_event.gesture_event_presence").id,
            "res_model": "event.event", "res_id": demain.id,
        })
        r = pastille.with_user(self.eleve).taper("app")
        self.assertEqual(r["statut"], "refused")
        self.assertIn("pendant la séance", r["message"])

    def test_la_pastille_signee_ne_note_pas_de_presence(self):
        r = self.porte.with_user(self.eleve).taper("signed")
        self.assertEqual(r["statut"], "refused")
        self.assertFalse(self._inscription())
