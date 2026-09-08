# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .commun import CasPlan


@tagged("post_install", "-at_install")
class TestAcces(CasPlan):

    def test_tout_utilisateur_interne_lit(self):
        plan = self.plan.with_user(self.lecteur)
        self.assertEqual(plan.name, "Banc")
        self.assertEqual(len(plan.zone_ids), 2)
        self.assertEqual(sorted(plan.element_ids.mapped("name")), ["AP-01", "P-01", "SW-01"])
        self.assertEqual(len(plan.lien_ids), 1)

    def test_le_lecteur_n_ecrit_pas(self):
        with self.assertRaises(AccessError):
            self.plan.with_user(self.lecteur).write({"name": "Autre"})
        with self.assertRaises(AccessError):
            self.poste.with_user(self.lecteur).write({"x": 0})
        with self.assertRaises(AccessError):
            self.Zone.with_user(self.lecteur).create({"plan_id": self.plan.id, "name": "Z"})
        with self.assertRaises(AccessError):
            self.lien.with_user(self.lecteur).unlink()

    def test_la_gestion_ecrit(self):
        self.plan.write({"name": "Banc 2"})
        self.poste.write({"x": 50.0})
        self.assertEqual(self.poste.x, 50.0)

    def test_autre_societe_invisible(self):
        societe = self.env["res.company"].create({"name": "Ailleurs inc."})
        plan = self.env["bf.floorplan"].create({"name": "Chez l'autre",
                                                "company_id": societe.id})
        self.env["bf.floorplan.zone"].create({"plan_id": plan.id, "name": "Z"})
        Plan = self.env["bf.floorplan"].with_user(self.lecteur)
        self.assertNotIn(plan.id, Plan.search([]).ids)
        Zone = self.env["bf.floorplan.zone"].with_user(self.lecteur)
        self.assertFalse(Zone.search([("plan_id", "=", plan.id)]))
