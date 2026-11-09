# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestAcces(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.roue = cls.env["bf.wheel"].create({
            "name": "Roue d'atelier",
            "segment_ids": [(0, 0, {"name": "Café"}), (0, 0, {"name": "Thé"})],
        })
        cls.employe = new_test_user(cls.env, login="employe-roue", groups="base.group_user")
        cls.portail = new_test_user(cls.env, login="portail-roue", groups="base.group_portal")
        cls.resp = new_test_user(
            cls.env, login="resp2-roue",
            groups="base.group_user,bf_wheel.group_bf_wheel_manager")

    def test_un_employe_fait_tourner_mais_ne_cree_pas(self):
        resultat = self.roue.with_user(self.employe).action_spin()
        self.assertIn(resultat["winner_label"], ["Café", "Thé"])
        with self.assertRaises(AccessError):
            self.env["bf.wheel"].with_user(self.employe).create({"name": "La mienne"})
        with self.assertRaises(AccessError):
            self.roue.with_user(self.employe).write({"name": "Renommée"})

    def test_un_responsable_cree(self):
        roue = self.env["bf.wheel"].with_user(self.resp).create({
            "name": "Roue du responsable",
            "segment_ids": [(0, 0, {"name": "Un"})],
        })
        self.assertTrue(roue.id)
        roue.with_user(self.resp).write({"name": "Roue renommée"})

    def test_le_portail_ne_voit_rien_et_ne_tire_pas(self):
        with self.assertRaises(AccessError):
            self.roue.with_user(self.portail).read(["name"])
        with self.assertRaises(AccessError):
            self.roue.with_user(self.portail).action_spin()

    def test_le_portail_ne_lit_pas_le_registre(self):
        spin_id = self.roue.action_spin()["spin_id"]
        with self.assertRaises(AccessError):
            self.env["bf.wheel.spin"].with_user(self.portail).browse(spin_id).read(["winner_label"])

    def test_l_ecran_verifie_lui_meme_le_droit_de_lire(self):
        """`get_display_data` est publique, donc atteignable par RPC."""
        with self.assertRaises(AccessError):
            self.roue.with_user(self.portail).get_display_data()
