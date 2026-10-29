# -*- coding: utf-8 -*-
"""Le reçu officiel de don porte le nom légal du donateur."""

import io

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestRecu(TransactionCase):

    def _recu(self, partner):
        Recu = self.env["donation.tax.receipt"]
        return Recu.create({"partner_id": partner.id,
                            "type": Recu._fields["type"].selection[0][0]})

    def _html(self, partner):
        return self.env["ir.actions.report"]._render_qweb_html(
            "donation_base.report_donationtaxreceipt", self._recu(partner).ids)[0].decode()

    def _texte_pdf(self, partner):
        from odoo.tools.pdf import PdfFileReader
        pdf = self.env["ir.actions.report"].with_context(force_report_rendering=True)._render_qweb_pdf(
            "donation_base.report_donationtaxreceipt", self._recu(partner).ids)[0]
        if not pdf.startswith(b"%PDF"):
            self.skipTest("pas de rendu PDF sur cette base")
        return "\n".join(page.extract_text() for page in PdfFileReader(io.BytesIO(pdf)).pages)

    def test_nom_legal_du_donateur_sans_adresse(self):
        # Sans adresse, le gabarit d'adresse d'Odoo (widget « contact ») écrit
        # le nom de la fiche dans un bloc que seule la feuille de style masque :
        # il s'imprimait quand elle ne se chargeait pas (vu à l'essai). Le nom
        # d'usage ne doit être nulle part dans le reçu, pas même masqué.
        donateur = self.env["res.partner"].create({"name": "Sacha Exemple"})
        donateur.sudo().bf_legal_name = "Morgane Exemple-Légal"
        html = self._html(donateur)
        self.assertIn("Morgane Exemple-Légal", html)
        self.assertNotIn("Sacha Exemple", html)
        texte = self._texte_pdf(donateur)
        self.assertIn("Morgane Exemple-Légal", texte)
        self.assertNotIn("Sacha Exemple", texte)

    def test_l_adresse_est_toujours_imprimee(self):
        donateur = self.env["res.partner"].create(
            {"name": "Sacha Exemple", "street": "1 rue Essai", "city": "Sherbrooke"})
        donateur.sudo().bf_legal_name = "Morgane Exemple-Légal"
        html = self._html(donateur)
        self.assertEqual(html.count("1 rue Essai"), 1, "une seule adresse")
        self.assertNotIn("Sacha Exemple", html)

    def test_sans_nom_legal_le_nom_d_usage(self):
        donateur = self.env["res.partner"].create({"name": "Lou Exemple"})
        self.assertIn("Lou Exemple", self._html(donateur))
