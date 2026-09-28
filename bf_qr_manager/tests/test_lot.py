from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from .commun import monter


@tagged("post_install", "-at_install")
class TestLot(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)

    def test_le_lot_tire_des_vierges_numerotees(self):
        self.assertEqual(len(self.etiquettes), 10)
        self.assertEqual(self.etiquettes.mapped("qr_numero"), list(range(1, 11)))
        self.assertEqual(self.etiquettes[0].qr_reference, "TST-0001")
        self.assertTrue(all(self.etiquettes.mapped("qr_vierge")))
        self.assertEqual(len(set(self.etiquettes.mapped("code"))), 10)

    def test_l_adresse_imprimee_est_la_porte_q(self):
        tag = self.etiquettes[0]
        self.assertTrue(tag.url.endswith("/q/%s" % tag.code), tag.url)

    def test_un_second_lot_suit_la_numerotation_du_prefixe(self):
        lot2 = self.env["bf.qr.batch"].with_user(self.gestion).create({"prefixe": "tst", "quantite": 3})
        lot2.action_generer()
        self.assertEqual(lot2.prefixe, "TST")
        self.assertEqual(sorted(lot2.tag_ids.mapped("qr_numero")), [11, 12, 13])
        self.assertEqual((lot2.numero_premier, lot2.numero_dernier), (11, 13))

    def test_un_lot_ne_se_genere_qu_une_fois(self):
        with self.assertRaises(UserError):
            self.lot.with_user(self.gestion).action_generer()

    def test_seule_la_gestion_tire_un_lot(self):
        lot = self.env["bf.qr.batch"].create({"prefixe": "ZZ", "quantite": 2})
        with self.assertRaises(AccessError):
            lot.with_user(self.concierge).action_generer()

    def test_prefixe_et_quantite_bornes(self):
        for valeurs in ({"prefixe": "A B", "quantite": 2}, {"prefixe": "OK", "quantite": 0},
                        {"prefixe": "OK", "quantite": 5001}):
            lot = self.env["bf.qr.batch"].create(valeurs)
            with self.assertRaises(UserError):
                lot.with_user(self.gestion).action_generer()

    def test_un_lot_genere_ne_se_supprime_pas(self):
        with self.assertRaises(UserError):
            self.lot.unlink()
