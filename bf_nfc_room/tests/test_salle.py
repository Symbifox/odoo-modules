"""La porte de la salle : dire d'abord, agir sur un bouton, ne jamais chevaucher."""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSalle(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.moi = new_test_user(cls.env, login="salle-moi", groups="base.group_user")
        cls.autre = new_test_user(cls.env, login="salle-autre", groups="base.group_user")
        cls.salle = cls.env["bf.nfc.room"].create({"name": "Salle A", "durations": "30,60"})
        cls.porte = cls.env["bf.nfc.tag"].create({
            "name": "Porte A", "gesture_id": cls.env.ref("bf_nfc_room.gesture_room").id,
            "res_model": "bf.nfc.room", "res_id": cls.salle.id,
        })

    def _reserver(self, qui, debut, fin, **vals):
        return self.env["calendar.event"].with_user(qui).create(dict({
            "name": "Réunion", "start": debut, "stop": fin, "user_id": qui.id,
            "partner_ids": [(6, 0, [qui.partner_id.id])], "bf_nfc_room_id": self.salle.id}, **vals))

    def test_libre_on_propose_et_rien_n_est_pris(self):
        r = self.porte.with_user(self.moi).taper("app")
        self.assertEqual(r["statut"], "choice")
        self.assertEqual([c["cle"] for c in r["choix"]], ["prendre:30", "prendre:60"])
        self.assertFalse(self.salle.event_ids)

    def test_prendre_30_minutes_reserve_et_confirme(self):
        r = self.porte.with_user(self.moi).taper("app", choix="prendre:30")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        reservation = self.salle.event_ids
        self.assertEqual(len(reservation), 1)
        self.assertEqual(reservation.user_id, self.moi)
        self.assertTrue(reservation.bf_nfc_checkin)
        self.assertAlmostEqual((reservation.stop - reservation.start).total_seconds(), 1800, delta=2)

    def test_occupee_par_un_autre_une_information(self):
        maintenant = fields.Datetime.now()
        self._reserver(self.autre, maintenant - timedelta(minutes=5), maintenant + timedelta(minutes=40))
        r = self.porte.with_user(self.moi).taper("app")
        self.assertEqual(r["statut"], "info")
        self.assertIn(self.autre.name, r["message"])
        refus = self.porte.with_user(self.moi).taper("app", choix="prendre:30")
        self.assertEqual(refus["statut"], "refused")

    def test_ma_reservation_je_confirme_puis_je_libere(self):
        maintenant = fields.Datetime.now()
        reservation = self._reserver(self.moi, maintenant - timedelta(minutes=2), maintenant + timedelta(hours=1))
        q = self.porte.with_user(self.moi).taper("app")
        self.assertEqual([c["cle"] for c in q["choix"]], ["arrive:%s" % reservation.id, "liberer:%s" % reservation.id])
        self.porte.with_user(self.moi).taper("app", choix="arrive:%s" % reservation.id)
        self.assertTrue(reservation.bf_nfc_checkin)
        self.porte.with_user(self.moi).taper("app", choix="liberer:%s" % reservation.id)
        self.assertLessEqual(reservation.stop, fields.Datetime.now())

    def test_on_ne_libere_pas_la_reservation_d_un_autre(self):
        maintenant = fields.Datetime.now()
        sienne = self._reserver(self.autre, maintenant - timedelta(minutes=2), maintenant + timedelta(hours=1))
        r = self.porte.with_user(self.moi).taper("app", choix="liberer:%s" % sienne.id)
        self.assertEqual(r["statut"], "refused")

    def test_bornee_par_la_reservation_suivante(self):
        maintenant = fields.Datetime.now()
        self._reserver(self.autre, maintenant + timedelta(minutes=20), maintenant + timedelta(hours=1))
        r = self.porte.with_user(self.moi).taper("app")
        self.assertEqual(len(r["choix"]), 1)
        self.assertTrue(r["choix"][0]["cle"].startswith("prendre:"))
        self.assertIn("jusqu'à", r["choix"][0]["libelle"])

    def test_deux_reservations_ne_se_chevauchent_pas(self):
        maintenant = fields.Datetime.now()
        self._reserver(self.autre, maintenant, maintenant + timedelta(hours=1))
        with self.assertRaises(ValidationError):
            self._reserver(self.moi, maintenant + timedelta(minutes=30), maintenant + timedelta(hours=2))

    def test_le_ramasseur_libere_seulement_ce_que_personne_n_a_confirme(self):
        maintenant = fields.Datetime.now()
        oubliee = self._reserver(self.autre, maintenant - timedelta(minutes=15), maintenant + timedelta(minutes=45))
        salle_b = self.env["bf.nfc.room"].create({"name": "Salle B"})
        confirmee = self.env["calendar.event"].create({
            "name": "Confirmée", "start": maintenant - timedelta(minutes=15),
            "stop": maintenant + timedelta(minutes=45), "bf_nfc_room_id": salle_b.id,
            "bf_nfc_checkin": maintenant - timedelta(minutes=14)})
        self.env["bf.nfc.room"]._cron_liberer()
        self.assertFalse(oubliee.bf_nfc_room_id)
        self.assertEqual(confirmee.bf_nfc_room_id, salle_b)
        self.assertTrue(oubliee.exists(), "L'événement reste dans l'agenda.")

    def test_ni_differe_ni_signee(self):
        quand = (fields.Datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(self.porte.with_user(self.moi).taper("app", choix="prendre:30", quand=quand)["statut"], "refused")
        self.assertEqual(self.porte.with_user(self.moi).taper("signed", choix="prendre:30")["statut"], "refused")
        self.assertFalse(self.salle.event_ids)
