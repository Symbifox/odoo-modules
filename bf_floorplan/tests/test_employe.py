# -*- coding: utf-8 -*-
from odoo.tests import tagged

from .commun import CasPlan


@tagged("post_install", "-at_install")
class TestEmploye(CasPlan):

    def test_compteur_et_saut_au_plan(self):
        self.assertEqual(self.employe.floorplan_element_count, 1)
        action = self.employe.action_voir_sur_le_plan()
        self.assertEqual(action["res_model"], "bf.floorplan")
        self.assertEqual(action["res_id"], self.plan.id)
        self.assertEqual(action["context"]["bf_floorplan_surligne"], self.poste.id)

    def test_deux_postes_donnent_la_liste(self):
        self.Element.create({"plan_id": self.plan.id, "name": "P-02", "genre": "poste",
                             "x": 300.0, "y": 100.0, "employee_id": self.employe.id})
        self.assertEqual(self.employe.floorplan_element_count, 2)
        action = self.employe.action_voir_sur_le_plan()
        self.assertEqual(action["res_model"], "bf.floorplan.element")
        self.assertEqual(action["domain"], [("employee_id", "=", self.employe.id)])

    def test_profil_public_sans_droits_rh(self):
        """Qui n'a pas les droits RH lit le profil public : le bouton y est."""
        public = self.env["hr.employee.public"].with_user(self.lecteur).browse(self.employe.id)
        self.assertEqual(public.floorplan_element_count, 1)
        action = public.action_voir_sur_le_plan()
        self.assertEqual(action["res_model"], "bf.floorplan")
        self.assertEqual(action["context"]["bf_floorplan_surligne"], self.poste.id)
        self.Element.create({"plan_id": self.plan.id, "name": "P-02", "genre": "poste",
                             "x": 300.0, "y": 100.0, "employee_id": self.employe.id})
        self.assertEqual(public.action_voir_sur_le_plan()["res_model"], "bf.floorplan.element")

    def test_sans_poste(self):
        autre = self.env["hr.employee"].create({"name": "Sans poste"})
        self.assertEqual(autre.floorplan_element_count, 0)
