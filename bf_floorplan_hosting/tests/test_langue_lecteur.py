# -*- coding: utf-8 -*-
"""Les états lus de la gestion d'hébergement suivent la langue de qui lit le plan.

hosting_management écrit maintenant ses libellés de sélection en anglais (source),
le français vivant dans son `i18n/fr_CA.po`. Le plan lisait ces libellés par
`_fields[...].selection`, c'est-à-dire la SOURCE : un usager fr_CA voyait
« In repair » sur un plan français. `_infos()` les lit désormais par
`_description_selection(self.env)`, traduit dans la langue de l'environnement.

Chaque essai lit le même élément en fr_CA (le libellé français) puis en en_CA
(la source anglaise). Les essais activent fr_CA et en_CA : selon la base, en_US
est absente ou seule.
"""
from odoo.tests import tagged

from odoo.addons.bf_floorplan.tests.commun import CasPlan


@tagged("post_install", "-at_install")
class TestLangue(CasPlan):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["hosting_management"], ["fr_CA"], overwrite=True)
        cls.gestion.groups_id = [(4, cls.env.ref("hosting_management.group_hosting_manager").id)]
        cls.client = cls.env["res.partner"].create({"name": "Client langue"})
        cls.appareil = cls.env["hosting.endpoint"].create({
            "name": "PC-LANGUE-01", "partner_id": cls.client.id,
            "endpoint_type": "workstation", "lifecycle_state": "in_repair",
            "os": "linux_other",
        })
        cls.serveur = cls.env["hosting.server"].create({
            "name": "Serveur langue", "code": "SRV-LANGUE", "hostname": "langue.local",
            "server_type": "development", "state": "decommissioned",
        })

    def _infos(self, element, lang):
        return element.with_context(lang=lang)._infos()

    def test_systeme_de_l_appareil_traduit(self):
        """element.py:112 : le système (`os`) se lit dans la langue du plan."""
        self.poste.endpoint_id = self.appareil
        self.assertIn("Linux (autre)", self._infos(self.poste, "fr_CA"))
        self.assertIn("Linux (other)", self._infos(self.poste, "en_CA"))
        self.assertNotIn("Linux (other)", self._infos(self.poste, "fr_CA"))

    def test_cycle_de_vie_de_l_appareil_traduit(self):
        """element.py:116 : l'état du cycle de vie (`lifecycle_state`) se lit traduit."""
        self.poste.endpoint_id = self.appareil
        self.assertIn("En réparation", self._infos(self.poste, "fr_CA"))
        self.assertIn("In repair", self._infos(self.poste, "en_CA"))
        self.assertNotIn("In repair", self._infos(self.poste, "fr_CA"))

    def test_etat_du_serveur_traduit(self):
        """element.py:126 : l'état du serveur (`state`) se lit traduit."""
        self.commutateur.server_id = self.serveur
        self.assertIn("Décommissionné", self._infos(self.commutateur, "fr_CA"))
        self.assertIn("Decommissioned", self._infos(self.commutateur, "en_CA"))
        self.assertNotIn("Decommissioned", self._infos(self.commutateur, "fr_CA"))
