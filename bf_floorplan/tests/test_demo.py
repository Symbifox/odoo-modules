# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDemo(TransactionCase):
    """Les données de démonstration, quand elles sont chargées : le plan
    tient debout, les zones se déduisent, l'occupation se compte."""

    def setUp(self):
        super().setUp()
        self.plan = self.env.ref("bf_floorplan.demo_plan_rdc", raise_if_not_found=False)
        if not self.plan:
            self.skipTest("données de démonstration non chargées")

    def test_le_plan_de_demo(self):
        self.assertEqual((self.plan.largeur, self.plan.profondeur), (1800.0, 1200.0))
        self.assertTrue(self.plan.fond)
        self.assertEqual(len(self.plan.zone_ids), 7)
        self.assertEqual(len(self.plan.element_ids), 21)
        self.assertEqual(len(self.plan.lien_ids), 5)

    def test_les_zones_se_deduisent(self):
        par_nom = {e.name: e for e in self.plan.element_ids}
        self.assertEqual(par_nom["P-01"].zone_id.name, "Aire ouverte")
        self.assertEqual(par_nom["SW-01"].zone_id.name, "Local technique")
        self.assertEqual(par_nom["D-01"].zone_id.name, "Direction")
        self.assertEqual(par_nom["Écran Fleuve"].zone_id.name, "Salle Fleuve")
        self.assertEqual(par_nom["AP-02"].zone_id.name, "Entreposage")

    def test_l_occupation(self):
        zones = {z.name: z for z in self.plan.zone_ids}
        self.assertEqual((zones["Aire ouverte"].occupes, zones["Aire ouverte"].capacite), (3, 8))
        self.assertEqual((zones["Direction"].occupes, zones["Direction"].capacite), (1, 2))
        self.assertEqual(self.plan.occupes, 4)

    def test_le_rendu_et_l_export_passent(self):
        d = self.plan.rendu()
        self.assertTrue(d["fond"])
        self.assertEqual(len(d["liens"]), 5)
        self.assertIn("<mxfile", self.plan.exporter_mxgraph())
