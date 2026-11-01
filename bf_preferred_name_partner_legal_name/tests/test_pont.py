# -*- coding: utf-8 -*-
"""Le « Legal name » de `partner_legal_name` ne s'offre plus sur la fiche d'une personne."""

from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestPontPartnerLegalName(TransactionCase):

    def _champ(self, lang):
        arch = self.env["res.partner"].with_context(lang=lang).get_views(
            [(False, "form")])["views"]["form"]["arch"]
        noeuds = etree.fromstring(arch).xpath("//field[@name='legal_name']")
        self.assertEqual(len(noeuds), 1)
        return noeuds[0]

    def test_masque_pour_les_particuliers(self):
        self.assertEqual(self._champ("en_US").get("invisible"), "not is_company")

    def test_libelle_de_societe(self):
        self.assertEqual(self._champ("en_US").get("string"), "Company legal name")
        if self.env["res.lang"]._lang_get("fr_CA").active:
            self.assertEqual(self._champ("fr_CA").get("string"), "Nom légal de la société")
