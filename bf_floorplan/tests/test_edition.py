# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .commun import CasPlan


@tagged("post_install", "-at_install")
class TestEdition(CasPlan):

    def test_deplacer_cale_sur_la_grille(self):
        d = self.plan.deplacer("element", self.poste.id, 212.0, 137.0)
        self.assertEqual((self.poste.x, self.poste.y), (200.0, 125.0))
        self.assertIn("elements", d)

    def test_deplacer_reste_dans_le_plan(self):
        self.plan.deplacer("element", self.poste.id, 5000.0, -300.0)
        self.assertEqual(self.poste.x, 1000.0 - 160.0)
        self.assertEqual(self.poste.y, 0.0)

    def test_deplacer_une_zone(self):
        self.plan.deplacer("zone", self.z_tech.id, 300.0, 300.0)
        self.assertEqual((self.z_tech.x, self.z_tech.y), (300.0, 300.0))
        # le local est parti : le commutateur n'est plus dans aucune zone
        self.assertFalse(self.commutateur.zone_id)

    def test_redimensionner(self):
        self.plan.redimensionner("zone", self.z_tech.id, 207.0, 5.0)
        self.assertEqual((self.z_tech.w, self.z_tech.h), (200.0, 25.0))
        # jamais au-delà du bord droit
        self.plan.redimensionner("zone", self.z_tech.id, 9000.0, 300.0)
        self.assertEqual(self.z_tech.w, 400.0)

    def test_tourner(self):
        self.plan.tourner(self.poste.id)
        self.assertEqual(self.poste.rotation, "90")
        with self.assertRaises(UserError):
            self.plan.tourner(self.z_tech.id)  # une zone n'est pas un élément

    def test_poser_un_element_centre(self):
        d = self.plan.poser("element", "imprimante", 500.0, 400.0)
        neuf = self.Element.browse(d["neuf"]["id"])
        self.assertEqual(d["neuf"]["sorte"], "element")
        self.assertEqual(neuf.genre, "imprimante")
        # 60 × 60 centré sur (500, 400) → coin (470, 370), calé → (475, 375)
        self.assertEqual((neuf.x, neuf.y), (475.0, 375.0))
        self.assertEqual(neuf.name, "Imprimante 1")
        self.assertEqual(neuf.zone_id, self.z_ouvert)

    def test_poser_une_zone(self):
        d = self.plan.poser("zone", "reunion", 800.0, 600.0)
        neuf = self.Zone.browse(d["neuf"]["id"])
        self.assertEqual(neuf.genre, "reunion")
        self.assertEqual(neuf.name, "Salle de réunion 1")
        # 400 × 300 centré sur (800, 600) : coin (600, 450), qui déborde à
        # droite et se trouve ramené au bord
        self.assertEqual((neuf.x, neuf.y), (600.0, 450.0))

    def test_poser_numerote(self):
        self.plan.poser("element", "poste", 500.0, 300.0)
        d = self.plan.poser("element", "poste", 500.0, 600.0)
        self.assertEqual(self.Element.browse(d["neuf"]["id"]).name, "Poste de travail 3")

    def test_poser_refuse_l_inconnu(self):
        with self.assertRaises(UserError):
            self.plan.poser("element", "licorne", 1, 1)
        with self.assertRaises(UserError):
            self.plan.poser("zone", "poste", 1, 1)
        with self.assertRaises(UserError):
            self.plan.poser("truc", "poste", 1, 1)

    def test_retirer_emporte_les_liens_et_le_dit(self):
        avant = len(self.plan.message_ids)
        self.plan.retirer("element", self.borne.id)
        self.assertFalse(self.borne.exists())
        self.assertFalse(self.lien.exists())
        self.assertEqual(len(self.plan.message_ids), avant + 1)
        self.assertIn("AP-01", self.plan.message_ids[0].body)

    def test_lier_et_delier(self):
        d = self.plan.lier(self.poste.id, self.commutateur.id, "fibre")
        self.assertEqual(len(d["liens"]), 2)
        lien = self.Lien.search([("src_id", "=", self.poste.id)])
        self.assertEqual(lien.genre, "fibre")
        d = self.plan.delier(lien.id)
        self.assertEqual(len(d["liens"]), 1)

    def test_lier_refuse_le_doublon_dans_les_deux_sens(self):
        with self.assertRaises(ValidationError):
            self.plan.lier(self.borne.id, self.commutateur.id)

    def test_lier_refuse_une_nature_inconnue(self):
        with self.assertRaises(UserError):
            self.plan.lier(self.poste.id, self.commutateur.id, "telepathie")

    def test_identifiant_invalide_refuse_lisiblement(self):
        with self.assertRaises(UserError):
            self.plan.deplacer("element", "abc", 0, 0)
        with self.assertRaises(UserError):
            self.plan.delier("abc")

    def test_lier_refuse_sur_soi(self):
        with self.assertRaises(ValidationError):
            self.plan.lier(self.borne.id, self.borne.id)

    def test_forme_d_un_autre_plan_refusee(self):
        autre = self.Plan.create({"name": "Ailleurs"})
        with self.assertRaises(UserError):
            autre.deplacer("element", self.poste.id, 0, 0)
        with self.assertRaises(UserError):
            autre.lier(self.poste.id, self.borne.id)
        with self.assertRaises(UserError):
            autre.delier(self.lien.id)

    def test_plan_fige_refuse(self):
        self.plan.action_figer()
        self.assertTrue(self.plan.verrouille)
        self.assertFalse(self.plan._modifiable())
        with self.assertRaises(UserError):
            self.plan.deplacer("element", self.poste.id, 0, 0)
        self.plan.action_rouvrir()
        self.assertTrue(self.plan._modifiable())
        self.plan.deplacer("element", self.poste.id, 0, 0)

    def test_plan_archive_refuse(self):
        self.plan.active = False
        with self.assertRaises(UserError):
            self.plan.poser("element", "poste", 1, 1)

    def test_lecteur_ne_dessine_pas(self):
        plan = self.plan.with_user(self.lecteur)
        self.assertFalse(plan._modifiable())
        with self.assertRaises(AccessError):
            plan.deplacer("element", self.poste.id, 0, 0)
        with self.assertRaises(AccessError):
            plan.poser("element", "poste", 1, 1)
