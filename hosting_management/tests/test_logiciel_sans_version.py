# -*- coding: utf-8 -*-
"""Les logiciels sans notion de version.

Ce que ces essais tiennent en place :

* le filtre « Version à renseigner » rend les vrais oublis, et eux seuls : une
  fiche SaaS sans version n'y figure pas, une fiche auto-hébergée sans version
  y figure. Avant la case, les deux se confondaient (de vrais oublis se
  cachaient parmi les SaaS) ;
* le domaine essayé est celui de la vue, pas une copie : un filtre retouché
  dans le XML sans l'essai ne passerait pas.
"""

import ast

from lxml import etree

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLogicielSansVersion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Logiciel = cls.env["hosting.software"]
        Service = cls.env["hosting.service"]
        client = cls.env["res.partner"].create({"name": "Client de banc", "is_company": True})
        cls.saas = Logiciel.create({
            "name": "Courriel de banc (SaaS)", "code": "TSV1",
            "software_type": "saas", "versionless": True,
        })
        cls.auto = Logiciel.create({
            "name": "PBX de banc", "code": "TSV2", "software_type": "self_hosted",
        })
        version = cls.env["hosting.software.version"].create({
            "software_id": cls.auto.id, "version": "20.6.0", "support_status": "supported",
        })
        cls.fiche_saas = Service.create({
            "name": "Courriel de banc", "partner_id": client.id, "software_id": cls.saas.id,
        })
        cls.oubli = Service.create({
            "name": "PBX de banc oublié", "partner_id": client.id, "software_id": cls.auto.id,
        })
        cls.renseignee = Service.create({
            "name": "PBX de banc renseigné", "partner_id": client.id,
            "software_id": cls.auto.id, "installed_version_id": version.id,
        })

    def _domaine_du_filtre(self, xmlid, nom):
        arch = self.env.ref(xmlid).arch_db
        filtre = etree.fromstring(arch.encode()).xpath(f"//filter[@name='{nom}']")
        self.assertEqual(len(filtre), 1, f"filtre {nom} introuvable dans {xmlid}")
        return ast.literal_eval(filtre[0].get("domain"))

    def test_filtre_version_a_renseigner(self):
        domaine = self._domaine_du_filtre(
            "hosting_management.hosting_service_view_search", "filter_version_missing")
        mes_fiches = self.fiche_saas | self.oubli | self.renseignee
        trouvees = self.env["hosting.service"].search(domaine + [("id", "in", mes_fiches.ids)])
        self.assertEqual(trouvees, self.oubli)

    def test_filtre_logiciels_sans_version(self):
        domaine = self._domaine_du_filtre(
            "hosting_management.hosting_software_view_search", "filter_versionless")
        trouves = self.env["hosting.software"].search(
            domaine + [("id", "in", (self.saas | self.auto).ids)])
        self.assertEqual(trouves, self.saas)

    def test_la_fiche_suit_son_logiciel(self):
        self.assertTrue(self.fiche_saas.software_versionless)
        self.assertFalse(self.oubli.software_versionless)
        self.assertFalse(self.fiche_saas.update_available)
