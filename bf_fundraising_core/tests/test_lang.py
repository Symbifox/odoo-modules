"""The Fundraising app menu carries its name in every installed language.

The menu belongs to the OCA donation module: its catalogue gave it "Don" in
French, so French users never read this module's own name.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDonationMenuName(TransactionCase):

    def test_the_menu_is_named_in_each_language(self):
        Lang = self.env["res.lang"]
        Lang._activate_lang("fr_CA")
        Lang._activate_lang("en_US")
        menu = self.env.ref("donation.donation_top_menu")
        menu.update_field_translations("name", {"fr_CA": "Don"})
        self.env["bf.solicit.code"]._bf_name_donation_menu()
        self.assertEqual(menu.with_context(lang="fr_CA").name, "Levée de fonds")
        self.assertEqual(menu.with_context(lang="en_US").name, "Fundraising")
