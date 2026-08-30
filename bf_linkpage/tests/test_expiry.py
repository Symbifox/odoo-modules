"""L'expiration : ce qui ferme une page que personne ne révoque."""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_linkpage", "post_install", "-at_install")
class TestExpiry(TransactionCase):

    def test_page_ponctuelle_armee_a_la_creation(self):
        """Une page ponctuelle sans expiration est l'angle mort qu'on refuse.

        Le test échoue si quelqu'un retire l'armement par défaut de `create` :
        on obtiendrait alors une page publique sans propriétaire qui reste
        ouverte parce que personne ne repasse.
        """
        page = self.env["bf.linkpage"].create({
            "name": "Ponctuelle", "kind": "oneoff",
        })
        self.assertTrue(page.date_expiry, "une page ponctuelle doit porter une expiration")
        self.assertGreater(page.date_expiry, fields.Datetime.now())

    def test_page_rattachee_sans_expiration(self):
        partner = self.env["res.partner"].create({"name": "Titulaire"})
        page = self.env["bf.linkpage"].create({
            "name": "Rattachée", "kind": "owner", "partner_id": partner.id,
        })
        self.assertFalse(page.date_expiry)

    def test_expiration_ferme_sans_cron(self):
        """L'expiration est une DATE lue à l'affichage, pas un état maintenu.

        Aucun cron ne tourne dans ce test : si `is_live` dépendait d'un travail
        planifié, la page resterait en ligne ici et le test échouerait.
        """
        partner = self.env["res.partner"].create({"name": "Titulaire 2"})
        page = self.env["bf.linkpage"].create({
            "name": "Expirante", "kind": "owner", "partner_id": partner.id,
            "state": "published",
            "date_expiry": fields.Datetime.now() + timedelta(days=1),
        })
        self.assertTrue(page.is_live)
        page.date_expiry = fields.Datetime.now() - timedelta(seconds=1)
        self.assertTrue(page.is_expired)
        self.assertFalse(page.is_live)

    def test_recherche_sur_expiree(self):
        partner = self.env["res.partner"].create({"name": "Titulaire 3"})
        vivante = self.env["bf.linkpage"].create({
            "name": "Vivante", "kind": "owner", "partner_id": partner.id,
            "date_expiry": fields.Datetime.now() + timedelta(days=5),
        })
        morte = self.env["bf.linkpage"].create({
            "name": "Morte", "kind": "owner", "partner_id": partner.id,
            "date_expiry": fields.Datetime.now() - timedelta(days=5),
        })
        expirees = self.env["bf.linkpage"].search([("is_expired", "=", True)])
        self.assertIn(morte, expirees)
        self.assertNotIn(vivante, expirees)

    def test_parametre_de_delai_invalide_ne_bloque_pas_la_creation(self):
        """Mesuré au QA du 2026-08-30 : un paramètre mal saisi faisait remonter
        `ValueError: invalid literal for int()` jusqu'à la création de la page.
        Un réglage fautif ne doit pas empêcher de travailler ; il doit retomber
        sur le délai par défaut."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_linkpage.oneoff_expiry_days", "quatre-vingt-dix")
        page = self.env["bf.linkpage"].create({"name": "Ponct", "kind": "oneoff"})
        self.assertTrue(page.date_expiry)
        self.assertGreater(page.date_expiry, fields.Datetime.now())

    def test_parametre_de_delai_negatif_retombe_sur_le_defaut(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_linkpage.oneoff_expiry_days", "-5")
        page = self.env["bf.linkpage"].create({"name": "Ponct2", "kind": "oneoff"})
        self.assertGreater(page.date_expiry, fields.Datetime.now())
