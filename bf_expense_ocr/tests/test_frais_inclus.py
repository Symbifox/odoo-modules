# -*- coding: utf-8 -*-
"""🔴 Un frais imprimé dans le bloc des taxes n'est pas toujours une taxe en plus.

Les frais environnementaux du Québec (EHF), les frais de recyclage et les
consignes s'impriment avec les taxes ET sont déjà compris dans le sous-total.
Le garde-fou les additionnait une deuxième fois, le reçu ne balançait plus, et
une lecture PARFAITEMENT juste se faisait refuser.

Vécu le 2026-09-18 sur un vrai reçu de détaillant d'informatique :
la photo brute était trop floue pour que le modèle lise la ligne EHF, donc le
défaut dormait. Recadrée, la ligne devenait lisible, et la dépense passait à
« à vérifier » trois fois sur trois. Une image plus nette rendait le résultat
PIRE : c'est le garde-fou qui était faux, pas la lecture.
"""

from odoo.tests import tagged

from .common import BancLecture


@tagged("post_install", "-at_install", "bf_expense_ocr")
class TestFraisInclus(BancLecture):

    #: Le reçu réel, au sou près. 1 499,00 + 0,60 + 29,99 + 0,60 = 1 530,19 :
    #: l'EHF de 1,20 est la somme des deux frais de 0,60, DÉJÀ dans le sous-total.
    RECU_AVEC_FRAIS_INCLUS = {
        "merchant_name": "Détaillant d'informatique",
        "date": "2025-11-29",
        "currency": "CAD",
        "subtotal": 1530.19,
        "gst": 76.51,
        "qst": 152.64,
        "other_taxes": 1.20,
        "tip": None,
        "total": 1759.34,
        "confidence": 0.95,
    }

    def test_le_recu_avec_frais_inclus_passe(self):
        """Le cas qui a révélé le défaut. Avant le correctif : « doubt »."""
        depense = self._depense_vierge()
        with self._passerelle(self.RECU_AVEC_FRAIS_INCLUS):
            self.assertTrue(depense.action_ocr_scan(),
                            "une lecture juste ne doit pas être refusée")
        self.assertEqual(depense.ocr_state, "done")
        self.assertAlmostEqual(depense.total_amount_currency, 1759.34, places=2)
        self.assertAlmostEqual(depense.tip_amount_currency, 0.0, places=2)
        self.assertFalse(depense.ocr_error_message)

    def test_un_frais_inclus_avec_un_pourboire_imprime(self):
        """Le frais est dans le sous-total ET il y a un pourboire au stylo."""
        recu = dict(self.RECU_JUSTE, subtotal=100.00, gst=5.00, qst=9.98,
                    other_taxes=2.00, tip=15.00, total=129.98)
        depense = self._depense_vierge()
        with self._passerelle(recu):
            self.assertTrue(depense.action_ocr_scan())
        self.assertEqual(depense.ocr_state, "done")
        self.assertAlmostEqual(depense.tip_amount_currency, 15.00, places=2)

    def test_une_vraie_taxe_en_plus_passe_toujours(self):
        """⚠️ Le correctif ne doit pas casser la lecture littérale.

        Ici l'« autre taxe » s'ajoute vraiment au sous-total, et c'est la
        PREMIÈRE hypothèse qui doit gagner.
        """
        recu = dict(self.RECU_JUSTE, other_taxes=1.00, total=27.30)
        depense = self._depense_vierge()
        with self._passerelle(recu):
            self.assertTrue(depense.action_ocr_scan())
        self.assertEqual(depense.ocr_state, "done")
        self.assertAlmostEqual(depense.tip_amount_currency, 3.10, places=2)
        self.assertAlmostEqual(depense.total_amount_currency, 27.30, places=2)

    def test_les_deux_hypotheses_ne_sauvent_pas_un_recu_faux(self):
        """Essayer deux lectures n'est pas une porte ouverte.

        Un reçu qui ne balance sous AUCUNE des deux doit toujours être refusé,
        sinon le correctif aurait transformé le garde-fou en passoire.

        ⚠️ Le total choisi est INFÉRIEUR au détail. Un total trop GRAND ne
        prouve rien : l'écart se lit alors comme un pourboire et la première
        hypothèse l'accepte, à juste titre. Premier jet de cet essai faux pour
        cette raison exacte.
        """
        recu = dict(self.RECU_AVEC_FRAIS_INCLUS, total=1700.00)
        depense = self._depense_vierge()
        with self._passerelle(recu):
            self.assertFalse(depense.action_ocr_scan())
        self.assertEqual(depense.ocr_state, "doubt")
        self.assertAlmostEqual(depense.total_amount_currency, 0.0, places=2)
        self.assertTrue(depense.ocr_error_message)

    def test_le_motif_rendu_est_celui_de_la_lecture_litterale(self):
        """Quand les deux échouent, le motif doit parler du reçu tel qu'imprimé."""
        recu = dict(self.RECU_AVEC_FRAIS_INCLUS, total=1700.00)
        _total, _pourboire, motif = self.env["hr.expense"]._ocr_reconcilie(recu)
        self.assertIn("1760.54", motif.replace(",", "."),
                      f"le motif doit citer la somme littérale : {motif}")

    def test_sans_autres_taxes_rien_ne_change(self):
        """Le chemin d'avant, intact : une seule hypothèse, un seul refus."""
        recu = dict(self.RECU_JUSTE, other_taxes=None, total=41.30)
        depense = self._depense_vierge()
        with self._passerelle(recu):
            self.assertFalse(depense.action_ocr_scan())
        self.assertEqual(depense.ocr_state, "doubt")

    def test_un_frais_a_zero_ne_declenche_pas_la_seconde_hypothese(self):
        recu = dict(self.RECU_JUSTE, other_taxes=0.0)
        depense = self._depense_vierge()
        with self._passerelle(recu):
            self.assertTrue(depense.action_ocr_scan())
        self.assertAlmostEqual(depense.tip_amount_currency, 3.10, places=2)
