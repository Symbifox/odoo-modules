# -*- coding: utf-8 -*-
"""L'écriture : deux lignes de charge, et le pourboire hors des taxes."""

from odoo.tests import tagged

from .common import BancPourboire


@tagged("post_install", "-at_install", "bf_expense_tip")
class TestEcriture(BancPourboire):

    # ------------------------------------------------------------------
    # L'employé avance l'argent
    # ------------------------------------------------------------------

    def test_facture_fournisseur_deux_lignes_de_charge(self):
        depense = self._depense(mode="own_account")
        feuille = self._rapport(depense)
        ecriture = feuille.account_move_ids
        self.assertEqual(len(ecriture), 1)

        charges = self._charges(ecriture)
        self.assertEqual(len(charges), 2, "le repas et le pourboire")

        repas = charges.filtered("tax_ids")
        pourboire = charges - repas
        self.assertEqual(len(repas), 1)
        self.assertEqual(len(pourboire), 1)
        self.assertAlmostEqual(repas.balance, 20.18, places=2)
        self.assertAlmostEqual(pourboire.balance, 3.10, places=2)
        self.assertFalse(pourboire.tax_ids, "le pourboire ne porte aucune taxe")

    def test_la_dette_envers_l_employe_reste_le_total_paye(self):
        depense = self._depense(mode="own_account")
        feuille = self._rapport(depense)
        passif = feuille.account_move_ids.line_ids.filtered(
            lambda l: l.account_id.account_type == "liability_payable")
        self.assertAlmostEqual(sum(passif.mapped("balance")), -26.30, places=2)

    def test_les_lignes_de_taxe_valent_celles_du_recu(self):
        depense = self._depense(mode="own_account")
        feuille = self._rapport(depense)
        taxes = feuille.account_move_ids.line_ids.filtered(
            lambda l: l.tax_line_id)
        self.assertAlmostEqual(sum(taxes.mapped("balance")), 3.02, places=2)

    def test_le_pourboire_n_est_base_d_aucune_taxe(self):
        """Le contrôle qui compte pour le rapport de taxes.

        Une ligne de pourboire qui se présenterait comme base de la TPS ou de
        la TVQ referait l'erreur qu'on vient de corriger, un étage plus bas.
        """
        depense = self._depense(mode="own_account")
        feuille = self._rapport(depense)
        charges = self._charges(feuille.account_move_ids)
        pourboire = charges.filtered(lambda l: not l.tax_ids)
        self.assertEqual(len(pourboire), 1)
        self.assertFalse(pourboire.tax_tag_ids,
                         "aucune étiquette de taxe sur le pourboire")
        self.assertTrue(charges.filtered("tax_ids").tax_tag_ids,
                        "le repas, lui, en porte — sinon le contrôle ne "
                        "contrôle rien")

    def test_l_ecriture_balance(self):
        depense = self._depense(mode="own_account")
        feuille = self._rapport(depense)
        self.assertAlmostEqual(
            sum(feuille.account_move_ids.line_ids.mapped("balance")), 0.0,
            places=2)
        self.assertEqual(feuille.account_move_ids.state, "posted")

    # ------------------------------------------------------------------
    # La carte de la compagnie
    # ------------------------------------------------------------------

    def test_carte_de_compagnie_deux_lignes_et_ca_balance(self):
        depense = self._depense(mode="company_account")
        feuille = self._rapport(depense)
        ecriture = feuille.account_move_ids
        charges = self._charges(ecriture)
        self.assertEqual(len(charges), 2)
        repas = charges.filtered("tax_ids")
        self.assertAlmostEqual(repas.balance, 20.18, places=2)
        self.assertAlmostEqual((charges - repas).balance, 3.10, places=2)
        self.assertAlmostEqual(
            sum(ecriture.line_ids.mapped("balance")), 0.0, places=2)

    def test_carte_de_compagnie_les_taxes_sont_celles_du_recu(self):
        depense = self._depense(mode="company_account")
        feuille = self._rapport(depense)
        taxes = feuille.account_move_ids.line_ids.filtered(lambda l: l.tax_line_id)
        self.assertAlmostEqual(sum(taxes.mapped("balance")), 3.02, places=2)

    # ------------------------------------------------------------------
    # Le compte du pourboire
    # ------------------------------------------------------------------

    def test_par_defaut_le_pourboire_va_au_compte_du_repas(self):
        depense = self._depense()
        feuille = self._rapport(depense)
        charges = self._charges(feuille.account_move_ids)
        self.assertEqual(len(charges.account_id), 1,
                         "un seul compte : les frais de représentation")

    def test_un_compte_designe_isole_le_pourboire(self):
        self.company_data["company"].expense_tip_account_id = self.compte_pourboires
        depense = self._depense()
        feuille = self._rapport(depense)
        pourboire = feuille.account_move_ids.line_ids.filtered(
            lambda l: l.account_id == self.compte_pourboires)
        self.assertAlmostEqual(pourboire.balance, 3.10, places=2)

    def test_la_ligne_de_pourboire_pointe_vers_la_depense(self):
        """La traçabilité : le comptable clique et tombe sur le reçu."""
        depense = self._depense(mode="own_account")
        feuille = self._rapport(depense)
        pourboire = self._charges(feuille.account_move_ids).filtered(
            lambda l: not l.tax_ids)
        self.assertEqual(pourboire.expense_id, depense)
