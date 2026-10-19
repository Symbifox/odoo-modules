# -*- coding: utf-8 -*-
"""L'enveloppe de l'assistant d'envoi suit la même règle que le gabarit."""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLiensLegaux(TransactionCase):

    def test_seuls_les_liens_declares_par_la_societe(self):
        societe = self.env.company
        if "brand_privacy_url" not in societe:
            self.skipTest("aucun champ de liens légaux sur la société")
        Wizard = self.env["hour.bank.send.wizard"]
        societe.write({"brand_privacy_url": False, "brand_terms_url": False})
        corps = str(Wizard._wrap_branded_body("<p>x</p>"))
        self.assertNotIn("/r/politique-de-confidentialite", corps)
        self.assertNotIn("Politique de confidentialit", corps)
        societe.write({"brand_privacy_url": "https://exemple.test/vie-privee",
                       "brand_terms_url": "https://exemple.test/conditions"})
        corps = str(Wizard._wrap_branded_body("<p>x</p>"))
        self.assertIn('href="https://exemple.test/vie-privee"', corps)
        self.assertIn('href="https://exemple.test/conditions"', corps)
        if "brand_email_tagline" in societe:
            societe.brand_email_tagline = False
            corps = str(Wizard._wrap_branded_body("<p>x</p>"))
        # Le slogan de l'éditeur n'est plus écrit en dur dans l'enveloppe.
        self.assertNotIn("souveraines pour vos donn", corps)
