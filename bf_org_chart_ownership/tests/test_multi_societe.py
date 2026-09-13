# -*- coding: utf-8 -*-
"""La cloison multi-société, éprouvée par un AUTRE utilisateur.

🔴 Un essai qui joue sous le même utilisateur et dans la même société que
celui qui a écrit la donnée ne verra JAMAIS un trou de cloisonnement. Tous
les essais de ce fichier changent d'utilisateur pour cette raison.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_org_chart")
class TestCloisonMultiSociete(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Societe = cls.env["res.company"]
        cls.maison = cls.env.company
        cls.voisine = Societe.create({"name": "Société voisine d'essai"})
        cls.voisin = cls.env["res.users"].create({
            "name": "Utilisateur de la voisine",
            "login": "voisin_orgchart_test",
            "company_id": cls.voisine.id,
            "company_ids": [(6, 0, [cls.voisine.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("base.group_partner_manager").id])],
        })
        P = cls.env["res.partner"]
        cls.holding = P.create({"name": "Holding cloison", "is_company": True})
        cls.filiale = P.create({"name": "Filiale cloison", "is_company": True})
        cls.lien_maison = cls.env["bf.ownership"].create({
            "owner_id": cls.holding.id, "owned_id": cls.filiale.id,
            "percent": 100, "company_id": cls.maison.id,
        })

    def test_le_voisin_ne_voit_pas_le_lien_de_la_maison(self):
        vus = self.env["bf.ownership"].with_user(self.voisin).search([])
        self.assertNotIn(self.lien_maison, vus,
                         "un gestionnaire d'une autre société lit la détention")

    def test_le_voisin_ne_peut_pas_le_lire_en_direct(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.lien_maison.with_user(self.voisin).read(["percent"])

    def test_la_maison_voit_le_sien(self):
        vus = self.env["bf.ownership"].search([])
        self.assertIn(self.lien_maison, vus)

    def test_un_lien_sans_societe_reste_visible(self):
        libre = self.env["bf.ownership"].create({
            "owner_id": self.holding.id, "owned_id": self.filiale.id,
            "percent": 50, "share_class": "sans societe", "company_id": False,
        })
        vus = self.env["bf.ownership"].with_user(self.voisin).search([])
        self.assertIn(libre, vus)

    def test_le_dessin_du_voisin_ne_montre_pas_nos_liens(self):
        """La cloison doit tenir jusque DANS la carte, pas seulement en liste."""
        carte = self.filiale.with_user(self.voisin)._org_chart_carte("detention")
        self.assertEqual([a for a in carte.aretes if a.etiquette == "100 %"], [],
                         "le dessin du voisin porte une arête qu'il ne peut pas lire")
