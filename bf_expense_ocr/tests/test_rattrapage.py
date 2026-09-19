# -*- coding: utf-8 -*-
"""🔴 Une panne de transport ne doit pas condamner une dépense non plus.

Même défaut que chez `bf_invoice_ocr`, avec une aggravation : le cron des
dépenses est ÉTEINT par décision de vie privée. Si la lecture au téléversement
meurt en transport et que rien ne repasse, personne ne repassera jamais. D'où
`_ocr_a_relire`, qui accepte de reprendre une pièce posée sur une dépense dont
la tentative précédente n'a pas abouti.

⚠️ Les essais interrogent le domaine RÉEL (`_ocr_domaine_rattrapage`) et la
décision RÉELLE (`_ocr_a_relire`), pas des copies réécrites ici.
"""

from odoo.tests import tagged

from .common import PNG_1x1, BancLecture


@tagged("post_install", "-at_install", "bf_expense_ocr")
class TestRattrapage(BancLecture):

    def _repechees(self, depenses):
        return self.env["hr.expense"].search(
            [("id", "in", depenses.ids)]
            + self.env["hr.expense"]._ocr_domaine_rattrapage()
        )

    def test_une_panne_de_transport_est_reprise(self):
        jamais = self._depense_vierge()
        pont_mort = self._depense_vierge()
        pont_mort.write({"ocr_state": "error",
                         "ocr_error_message": "Service OCR indisponible"})
        reprises = self._repechees(jamais + pont_mort)
        self.assertIn(pont_mort, reprises)
        self.assertIn(jamais, reprises)
        self.assertTrue(pont_mort._ocr_a_relire())

    def test_un_verdict_du_garde_fou_n_est_PAS_repris(self):
        """« À vérifier » est une décision, pas une panne. Et `error` avec une
        réponse brute non plus."""
        doute = self._depense_vierge()
        doute.write({"ocr_state": "doubt", "ocr_raw_response": "{}"})
        juge = self._depense_vierge()
        juge.write({"ocr_state": "error", "ocr_raw_response": '{"error": "x"}'})
        reprises = self._repechees(doute + juge)
        self.assertNotIn(doute, reprises)
        self.assertNotIn(juge, reprises)
        self.assertFalse(juge._ocr_a_relire())
        self.assertFalse(doute._ocr_a_relire())

    def _poser_une_piece(self, depense, nom="recu2.png"):
        """⚠️ `hr.expense.attach_document` du noyau lit `kwargs['attachment_ids']`
        et lève un KeyError sans lui. Il faut donc lui passer la pièce, comme le
        fait le téléversement réel."""
        piece = self.env["ir.attachment"].create({
            "name": nom, "res_model": "hr.expense", "res_id": depense.id,
            "datas": PNG_1x1, "mimetype": "image/png",
        })
        return depense.attach_document(attachment_ids=[piece.id])

    def test_une_photo_posee_apres_une_panne_relance_la_lecture(self):
        """Le vrai geste : on rejoint une pièce, et la lecture repart."""
        self.env.company.expense_ocr_auto = True
        depense = self._depense_vierge()
        depense.write({"ocr_state": "error",
                       "ocr_error_message": "Service OCR indisponible"})
        with self._passerelle(self.RECU_JUSTE) as espion:
            self._poser_une_piece(depense)
        self.assertEqual(len(espion.appels), 1, "la lecture doit repartir")
        self.assertEqual(depense.ocr_state, "done")

    def test_une_dependance_deja_lue_ne_repart_pas_sur_une_piece_de_plus(self):
        self.env.company.expense_ocr_auto = True
        depense = self._depense_vierge()
        depense.write({"ocr_state": "done", "ocr_raw_response": "{}"})
        with self._passerelle(self.RECU_JUSTE) as espion:
            self._poser_une_piece(depense)
        self.assertFalse(espion.appels, "on ne relit pas ce qui a déjà été lu")

    def test_le_cron_emploie_bien_ce_domaine(self):
        import inspect
        source = inspect.getsource(type(self.env["hr.expense"])._cron_ocr_batch)
        self.assertIn("_ocr_domaine_rattrapage", source)
