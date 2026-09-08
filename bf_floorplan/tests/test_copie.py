# -*- coding: utf-8 -*-
from odoo.tests import tagged

from .commun import CasPlan


@tagged("post_install", "-at_install")
class TestCopie(CasPlan):

    def test_dupliquer_refait_les_liens_sur_les_copies(self):
        self.plan.action_figer()
        copie = self.plan.copy()
        self.assertEqual(copie.name, "Banc (copie)")
        self.assertFalse(copie.verrouille)
        self.assertEqual(len(copie.zone_ids), 2)
        self.assertEqual(len(copie.element_ids), 3)
        self.assertEqual(len(copie.lien_ids), 1)
        lien = copie.lien_ids
        self.assertEqual(lien.src_id.plan_id, copie)
        self.assertEqual(lien.dst_id.plan_id, copie)
        self.assertEqual((lien.src_id.name, lien.dst_id.name), ("SW-01", "AP-01"))
        self.assertEqual(lien.name, "port 3")
        # l'original n'a pas bougé
        self.assertEqual(len(self.plan.lien_ids), 1)
        self.assertEqual(self.plan.lien_ids.src_id, self.commutateur)

    def test_un_element_archive_ne_casse_pas_la_copie(self):
        self.borne.active = False
        copie = self.plan.copy()
        self.assertEqual(len(copie.with_context(active_test=False).element_ids), 3)
        self.assertEqual(len(copie.lien_ids), 1)
        self.assertFalse(copie.lien_ids.dst_id.active)

    def test_les_zones_copiees_reprennent_leurs_elements(self):
        copie = self.plan.copy()
        poste = copie.element_ids.filtered(lambda e: e.name == "P-01")
        self.assertEqual(poste.zone_id.plan_id, copie)
        self.assertEqual(poste.zone_id.name, "Aire ouverte")
