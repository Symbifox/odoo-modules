# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""L'erreur du formulaire de don public suit la langue de la requête.

La source du module est maintenant anglaise ; le français vit dans
`i18n/fr_CA.po`. `create_web_donation` refuse le don quand aucun produit de don
n'est configuré : ce message, vu par le visiteur du formulaire public, était
une chaîne française passée par `_()` ; il passe désormais par `self.env._`
(donation_donation.py:42-46), dans la langue de l'environnement du site.

Les essais activent fr_CA et en_CA et épinglent la langue de chaque appel :
selon la base, en_US est absente ou seule.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        # Aucun produit de don utilisable : ni celui de la société, ni un produit
        # admissible au reçu (une copie de base peut en porter).
        cls.env.company.donation_credit_transfer_product_id = False
        cls.env["product.product"].search(
            [("donation_type", "!=", False), ("tax_receipt_ok", "=", True)]
        ).write({"active": False})
        cls.donateur = cls.env["res.partner"].create({"name": "Donateur langue"})

    def test_aucun_produit_de_don_en_francais(self):
        Donation = self.env["donation.donation"].with_context(lang="fr_CA")
        with self.assertRaisesRegex(UserError, "^Aucun produit de don n'est configuré"):
            Donation.create_web_donation(self.donateur.id, 10.0)

    def test_aucun_produit_de_don_en_anglais(self):
        Donation = self.env["donation.donation"].with_context(lang="en_CA")
        with self.assertRaisesRegex(UserError, "^No donation product is configured"):
            Donation.create_web_donation(self.donateur.id, 10.0)
