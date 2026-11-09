# -*- coding: utf-8 -*-
import json
from collections import Counter

from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from ..models import bf_wheel as module_roue


@tagged("post_install", "-at_install")
class TestRoue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.roue = cls.env["bf.wheel"].create({
            "name": "Tirage du midi",
            "segment_ids": [
                (0, 0, {"name": "Alice", "weight": 1}),
                (0, 0, {"name": "Bopha", "weight": 1}),
                (0, 0, {"name": "Chen", "weight": 1}),
            ],
        })

    def test_tirage_ecrit_le_registre(self):
        resultat = self.roue.action_spin()
        spin = self.env["bf.wheel.spin"].browse(resultat["spin_id"])
        self.assertEqual(spin.wheel_id, self.roue)
        self.assertIn(spin.winner_label, ["Alice", "Bopha", "Chen"])
        self.assertEqual(spin.winner_label, resultat["winner_label"])
        self.assertEqual(spin.candidate_count, 3)
        self.assertEqual(spin.weight_total, 3)
        self.assertEqual(spin.spun_by_id, self.env.user)
        self.assertEqual(spin.source, "manual")
        self.assertEqual(
            [c["label"] for c in spin.candidate_list()],
            ["Alice", "Bopha", "Chen"],
            "le registre garde la liste exacte du tirage, dans son ordre")
        self.assertEqual(spin.segment_id.name, spin.winner_label)

    def test_la_liste_rendue_est_celle_du_tirage(self):
        """L'écran doit redessiner la roue d'où le résultat est sorti."""
        resultat = self.roue.action_spin()
        self.assertEqual(
            [c["label"] for c in resultat["candidates"]],
            ["Alice", "Bopha", "Chen"])
        self.assertEqual(
            resultat["candidates"][resultat["winner_index"]]["label"],
            resultat["winner_label"],
            "le rang renvoyé pointe le gagnant DANS la liste renvoyée")

    def test_les_poids_pesent(self):
        """Un poids de 999 999 contre 1 ne laisse presque aucune chance au second."""
        tirages = Counter(self.env['bf.wheel']._draw_index([1, 999999]) for _ in range(400))
        self.assertLess(tirages[0], 10, "le poids de 1 sort une fois sur un million")
        self.assertGreater(tirages[1], 390)

    def test_un_poids_de_trois_vaut_trois_candidats(self):
        """Le tirage marche la liste : le billet tiré tombe dans la bonne part."""
        # On remplace le générateur du module par un billet choisi, ce qui laisse
        # voir la frontière des parts sans dépendre du hasard.
        poids = [2, 3, 1]  # parts : billets 0-1, 2-4, 5
        attendus = {0: 0, 1: 0, 2: 1, 3: 1, 4: 1, 5: 2}
        original = module_roue._rng
        try:
            for billet, rang in attendus.items():
                module_roue._rng = type("Faux", (), {"randrange": staticmethod(lambda _n, b=billet: b)})()
                self.assertEqual(self.env['bf.wheel']._draw_index(poids), rang,
                                 "billet %s" % billet)
        finally:
            module_roue._rng = original

    def test_roue_vide_refuse(self):
        vide = self.env["bf.wheel"].create({"name": "Roue vide"})
        with self.assertRaises(UserError):
            vide.action_spin()

    def test_retirer_le_gagnant_vide_la_roue(self):
        self.roue.remove_drawn = True
        comptes = []
        for _ in range(3):
            resultat = self.roue.action_spin()
            comptes.append(len(resultat["candidates"]))
        self.assertEqual(comptes, [3, 2, 1], "chaque tirage retire son gagnant")
        self.assertEqual(self.roue.available_count, 0)
        with self.assertRaises(UserError):
            self.roue.action_spin()
        gagnants = self.roue.spin_ids.mapped("winner_label")
        self.assertEqual(sorted(gagnants), ["Alice", "Bopha", "Chen"],
                         "trois tirages sans remise sortent les trois noms")
        self.roue.action_reset_drawn()
        self.assertEqual(self.roue.available_count, 3)
        self.assertEqual(self.roue.spin_count, 3, "remettre en jeu ne touche pas au registre")

    def test_sans_retrait_le_gagnant_reste_en_jeu(self):
        self.assertFalse(self.roue.remove_drawn)
        for _ in range(4):
            self.assertEqual(len(self.roue.action_spin()["candidates"]), 3)

    def test_donnees_de_l_ecran(self):
        data = self.roue.get_display_data()
        self.assertEqual(data["name"], "Tirage du midi")
        self.assertTrue(data["can_spin"])
        self.assertEqual([c["label"] for c in data["candidates"]], ["Alice", "Bopha", "Chen"])
        self.assertTrue(all(c["color"].startswith("#") for c in data["candidates"]))
        self.assertEqual(len({c["color"] for c in data["candidates"]}), 3,
                         "trois segments créés d'un geste prennent trois couleurs")

    def test_l_ecran_recoit_l_encre_de_chaque_etiquette(self):
        """🔴 Sans elle, l'étiquette partait avec `fill=""` et retombait au noir."""
        from odoo.addons.bf_live_base.models.color_utils import DARK_TEXT, LIGHT_TEXT, contrast_ratio
        for charge in (self.roue.get_display_data()["candidates"],
                       self.roue.action_spin()["candidates"]):
            self.assertTrue(charge)
            for candidat in charge:
                self.assertIn(candidat["text_color"], (DARK_TEXT, LIGHT_TEXT))
                self.assertGreaterEqual(
                    contrast_ratio(candidat["color"], candidat["text_color"]), 4.5,
                    "l'étiquette de %s se lirait sous le minimum" % candidat["label"])

    def test_poids_zero_refuse_par_la_base(self):
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.env["bf.wheel.segment"].create({
                    "wheel_id": self.roue.id, "name": "Zéro", "weight": 0})

    def test_poids_negatif_refuse_a_l_ecriture(self):
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.roue.segment_ids[0].weight = -3

    def test_candidats_json_relisible(self):
        resultat = self.roue.action_spin()
        spin = self.env["bf.wheel.spin"].browse(resultat["spin_id"])
        charge = json.loads(spin.candidates)
        self.assertEqual(sum(c["weight"] for c in charge), 3)
