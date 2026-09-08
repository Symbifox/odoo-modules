# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .commun import CasPlan


@tagged("post_install", "-at_install")
class TestGeometrie(CasPlan):

    def test_taille_par_defaut_selon_la_nature(self):
        self.assertEqual((self.poste.w, self.poste.h), (160.0, 80.0))
        self.assertEqual((self.commutateur.w, self.commutateur.h), (45.0, 25.0))
        self.assertEqual((self.borne.w, self.borne.h), (30.0, 30.0))

    def test_taille_explicite_respectee(self):
        el = self.Element.create({"plan_id": self.plan.id, "genre": "table",
                                  "x": 0, "y": 0, "w": 300.0, "h": 120.0})
        self.assertEqual((el.w, el.h), (300.0, 120.0))

    def test_onchange_nature_repose_la_taille(self):
        el = self.Element.new({"plan_id": self.plan.id, "genre": "poste"})
        el.genre = "baie"
        el._onchange_genre()
        self.assertEqual((el.w, el.h), (60.0, 100.0))

    def test_zone_deduite_du_centre(self):
        self.assertEqual(self.poste.zone_id, self.z_ouvert)
        self.assertEqual(self.commutateur.zone_id, self.z_tech)
        self.assertEqual(self.borne.zone_id, self.z_ouvert)

    def test_zone_suit_le_deplacement(self):
        self.poste.write({"x": 700.0, "y": 200.0})
        self.assertEqual(self.poste.zone_id, self.z_tech)
        self.poste.write({"x": 700.0, "y": 600.0})  # sous le local technique : rien
        self.assertFalse(self.poste.zone_id)

    def test_c_est_le_centre_qui_compte(self):
        """Le coin haut-gauche est hors du local, le centre dedans : l'élément
        est dans le local."""
        el = self.Element.create({"plan_id": self.plan.id, "genre": "poste",
                                  "x": 560.0, "y": 100.0})  # 560 + 80 = 640 > 600
        self.assertEqual(el.zone_id, self.z_tech)
        el.write({"x": 440.0})  # centre 520 : aire ouverte
        self.assertEqual(el.zone_id, self.z_ouvert)

    def test_zone_la_plus_petite_gagne(self):
        bureau = self.Zone.create({
            "plan_id": self.plan.id, "name": "Bureau dans l'aire", "genre": "bureau",
            "x": 50.0, "y": 50.0, "w": 300.0, "h": 200.0})
        self.assertEqual(self.poste.zone_id, bureau)
        # la zone redimensionnée ne contient plus le centre : l'élément revient
        # à l'aire ouverte
        bureau.write({"w": 100.0, "h": 100.0})
        self.assertEqual(self.poste.zone_id, self.z_ouvert)

    def test_occupation(self):
        self.assertEqual(self.z_ouvert.occupes, 1)
        self.assertEqual(self.plan.occupes, 1)
        self.assertEqual(self.plan.places, 4)
        self.poste.employee_id = False
        self.assertEqual(self.z_ouvert.occupes, 0)

    def test_rotation_cycle(self):
        self.assertEqual(self.poste.rotation, "0")
        for attendu in ("90", "180", "270", "0"):
            self.poste.action_tourner()
            self.assertEqual(self.poste.rotation, attendu)

    def test_refus_hors_plan(self):
        with self.assertRaises(ValidationError):
            self.Element.create({"plan_id": self.plan.id, "genre": "poste",
                                 "x": 950.0, "y": 100.0})
        with self.assertRaises(ValidationError):
            self.Zone.create({"plan_id": self.plan.id, "name": "Trop bas",
                              "x": 0.0, "y": 700.0, "w": 100.0, "h": 200.0})

    def test_refus_taille_nulle(self):
        with self.assertRaises(ValidationError):
            self.poste.write({"w": 0.0})
        with self.assertRaises(ValidationError):
            self.z_tech.write({"h": -5.0})

    def test_refus_dimensions_du_plan(self):
        with self.assertRaises(ValidationError):
            self.plan.write({"largeur": 0.0})
        with self.assertRaises(ValidationError):
            self.plan.write({"pas": 0.0})
        with self.assertRaises(ValidationError):
            self.plan.write({"profondeur": 200000.0})

    def test_nom_affiche(self):
        self.assertEqual(self.plan.display_name, "Banc (Atelier, étage 1)")
        sans = self.Plan.create({"name": "Nu"})
        self.assertEqual(sans.display_name, "Nu")
        anonyme = self.Element.create({"plan_id": self.plan.id, "genre": "imprimante",
                                       "x": 0, "y": 0})
        self.assertEqual(anonyme.display_name, "Imprimante")

    def test_lien_count(self):
        self.assertEqual(self.commutateur.lien_count, 1)
        self.assertEqual(self.poste.lien_count, 0)
