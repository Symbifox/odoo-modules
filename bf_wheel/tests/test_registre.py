# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestRegistre(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.roue = cls.env["bf.wheel"].create({
            "name": "Prix de présence",
            "claim_enabled": True,
            "claim_retention_days": 90,
            "segment_ids": [(0, 0, {"name": "Billet d'or", "weight": 1})],
        })
        cls.anim = new_test_user(cls.env, login="anim-roue", groups="base.group_user")
        cls.resp = new_test_user(
            cls.env, login="resp-roue",
            groups="base.group_user,bf_wheel.group_bf_wheel_manager")

    def _tirage(self):
        return self.env["bf.wheel.spin"].browse(self.roue.action_spin()["spin_id"])

    def test_un_tirage_ne_se_recrit_pas(self):
        spin = self._tirage()
        with self.assertRaises(UserError):
            spin.with_user(self.resp).write({"winner_label": "Moi"})
        with self.assertRaises(UserError):
            spin.with_user(self.resp).write({"date": fields.Datetime.now()})
        with self.assertRaises(UserError):
            spin.with_user(self.resp).write({"candidate_count": 99})
        self.assertEqual(spin.winner_label, "Billet d'or")

    def test_un_tirage_ne_s_efface_pas(self):
        spin = self._tirage()
        with self.assertRaises(UserError):
            spin.with_user(self.resp).unlink()
        self.assertTrue(spin.exists())

    def test_effacer_la_roue_emporte_son_registre(self):
        """La cascade se fait dans la base : la garde du modèle ne la voit pas."""
        spin = self._tirage()
        self.roue.unlink()
        self.assertFalse(spin.exists())

    def test_noter_la_personne_tiree(self):
        spin = self._tirage()
        spin.with_user(self.anim).action_claim("Jeanne Bélanger", "jeanne@example.org")
        spin.invalidate_recordset()
        self.assertEqual(spin.claim_name, "Jeanne Bélanger")
        self.assertEqual(spin.claim_contact, "jeanne@example.org")
        self.assertTrue(spin.claim_date)

    def test_une_coordonnee_se_note_une_fois(self):
        spin = self._tirage()
        spin.with_user(self.anim).action_claim("Jeanne Bélanger")
        with self.assertRaises(UserError):
            spin.with_user(self.anim).action_claim("Quelqu'un d'autre")
        with self.assertRaises(UserError):
            spin.with_user(self.anim).write({"claim_name": "Quelqu'un d'autre"})
        # Un responsable corrige.
        spin.with_user(self.resp).write({"claim_name": "Jeanne Bélanger-Roy"})
        spin.invalidate_recordset()
        self.assertEqual(spin.claim_name, "Jeanne Bélanger-Roy")

    def test_pas_de_coordonnees_quand_la_roue_n_en_demande_pas(self):
        muette = self.env["bf.wheel"].create({
            "name": "Sans prix",
            "segment_ids": [(0, 0, {"name": "Sujet A"})],
        })
        spin = self.env["bf.wheel.spin"].browse(muette.action_spin()["spin_id"])
        with self.assertRaises(UserError):
            spin.with_user(self.anim).action_claim("Jeanne")

    def test_un_nom_vide_n_est_pas_un_nom(self):
        spin = self._tirage()
        with self.assertRaises(UserError):
            spin.with_user(self.anim).action_claim("   ")

    def test_elagage_au_terme_du_delai(self):
        spin = self._tirage()
        spin.action_claim("Jeanne Bélanger", "jeanne@example.org")
        spin.sudo().write({"date": fields.Datetime.now() - timedelta(days=91)})

        elagues = self.env["bf.wheel.spin"]._gc_claims()
        self.assertGreaterEqual(elagues, 1)
        spin.invalidate_recordset()
        self.assertFalse(spin.claim_name)
        self.assertFalse(spin.claim_contact)
        self.assertFalse(spin.claim_date)
        self.assertFalse(spin.candidates, "les pseudonymes de la salle partent aussi")
        self.assertTrue(spin.purged)
        # Ce qui prouve le tirage reste.
        self.assertEqual(spin.winner_label, "Billet d'or")
        self.assertEqual(spin.candidate_count, 1)
        self.assertTrue(spin.date)

    def test_elagage_ne_touche_pas_ce_qui_est_dans_le_delai(self):
        spin = self._tirage()
        spin.action_claim("Jeanne Bélanger")
        spin.sudo().write({"date": fields.Datetime.now() - timedelta(days=89)})
        self.env["bf.wheel.spin"]._gc_claims()
        spin.invalidate_recordset()
        self.assertEqual(spin.claim_name, "Jeanne Bélanger")
        self.assertFalse(spin.purged)

    def test_delai_a_zero_ne_purge_rien(self):
        """Zéro ne veut pas dire « tout de suite » : il veut dire « jamais »."""
        self.roue.claim_retention_days = 0
        spin = self._tirage()
        spin.action_claim("Jeanne Bélanger")
        spin.sudo().write({"date": fields.Datetime.now() - timedelta(days=4000)})
        self.env["bf.wheel.spin"]._gc_claims()
        spin.invalidate_recordset()
        self.assertEqual(spin.claim_name, "Jeanne Bélanger")
        self.assertFalse(spin.purged)

    def test_elagage_ne_repasse_pas(self):
        spin = self._tirage()
        spin.action_claim("Jeanne Bélanger")
        spin.sudo().write({"date": fields.Datetime.now() - timedelta(days=91)})
        self.assertEqual(self.env["bf.wheel.spin"]._gc_claims(), 1)
        self.assertEqual(self.env["bf.wheel.spin"]._gc_claims(), 0,
                         "une ligne déjà élaguée ne se réécrit pas chaque nuit")

    def test_un_tirage_elague_ne_se_reclame_plus(self):
        spin = self._tirage()
        spin.sudo().write({"date": fields.Datetime.now() - timedelta(days=91),
                           "claim_name": "Jeanne"})
        self.env["bf.wheel.spin"]._gc_claims()
        spin.invalidate_recordset()
        with self.assertRaises(UserError):
            spin.with_user(self.resp).action_claim("Jeanne")
