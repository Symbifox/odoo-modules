"""Le prêt par pastille : un tapotement dans les cas courants, une question sinon."""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPret(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_nfc.fenetre_doublon_secondes", "0")
        cls.alice = new_test_user(cls.env, login="pret-alice", groups="base.group_user")
        cls.bruno = new_test_user(cls.env, login="pret-bruno", groups="base.group_user")
        cls.portable = cls.env["bf.nfc.equipment"].create({
            "name": "Portable de prêt", "place": "Armoire de l'accueil", "max_days": 3})
        cls.pastille = cls.env["bf.nfc.tag"].create({
            "name": "Portable", "gesture_id": cls.env.ref("bf_nfc_loan.gesture_loan").id,
            "res_model": "bf.nfc.equipment", "res_id": cls.portable.id,
        })

    def test_libre_il_est_pris_puis_rendu_sans_bouton(self):
        r = self.pastille.with_user(self.alice).taper("app")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self.portable.holder_id, self.alice)
        r = self.pastille.with_user(self.alice).taper("app")
        self.assertEqual(r["statut"], "ok")
        self.assertIn("Armoire de l'accueil", r["message"])
        self.assertFalse(self.portable.holder_id)
        self.assertEqual(len(self.portable.loan_ids), 1)
        self.assertTrue(self.portable.loan_ids.date_end)

    def test_chez_un_autre_une_question_puis_la_reprise(self):
        self.pastille.with_user(self.alice).taper("app")
        question = self.pastille.with_user(self.bruno).taper("app")
        self.assertEqual(question["statut"], "choice")
        self.assertIn(self.alice.name, question["message"])
        self.assertEqual(self.portable.holder_id, self.alice, "Une question ne change rien.")
        r = self.pastille.with_user(self.bruno).taper("app", choix="reprendre")
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self.portable.holder_id, self.bruno)
        ancien = self.portable.loan_ids.filtered(lambda l: l.user_id == self.alice)
        self.assertEqual(ancien.closed_by_id, self.bruno)

    def test_rendre_ce_qu_on_n_a_pas_est_refuse(self):
        menu_rendre = self.env["bf.nfc.tag"].create({
            "name": "Rendre", "gesture_id": self.env.ref("bf_nfc_loan.gesture_loan").id,
            "res_model": "bf.nfc.equipment", "res_id": self.portable.id,
            "params": '{"sens": "rendre"}',
        })
        r = menu_rendre.with_user(self.alice).taper("app")
        self.assertEqual(r["statut"], "refused")
        self.assertIn("rien à rendre", r["message"])

    def test_pris_en_differe_garde_son_heure(self):
        quand = fields.Datetime.now() - timedelta(hours=5)
        r = self.pastille.with_user(self.alice).taper("app", quand=quand.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(r["statut"], "ok", r.get("message"))
        self.assertEqual(self.portable.since.replace(microsecond=0), quand.replace(microsecond=0))
        self.assertTrue(self.portable.current_loan_id.offline)

    def test_la_pastille_signee_ne_prete_pas_au_compte_designe(self):
        r = self.pastille.with_user(self.alice).taper("signed")
        self.assertEqual(r["statut"], "refused")

    def test_rappel_une_seule_fois_apres_le_delai(self):
        self.pastille.with_user(self.alice).taper("app")
        self.portable.current_loan_id.date_start = fields.Datetime.now() - timedelta(days=4)
        self.env["bf.nfc.equipment"]._cron_rappels()
        self.env["bf.nfc.equipment"]._cron_rappels()
        activites = self.env["mail.activity"].search([
            ("res_model", "=", "bf.nfc.equipment"), ("res_id", "=", self.portable.id)])
        self.assertEqual(len(activites), 1)
        self.assertEqual(activites.user_id, self.alice)
