# -*- coding: utf-8 -*-
"""Le motif d'une lecture s'écrit dans la langue de qui lira la dépense.

`ocr_error_message` est STOCKÉ. Au bouton, c'est l'employé qui le déclenche et
le lit : sa langue est celle de l'appel. Au rattrapage planifié, l'appel tourne
sous l'usager du cron ; sans langue épinglée, le motif s'écrirait dans la
sienne. Même chose pour le nom de la copie recadrée.
"""

from odoo.tests import tagged

from .common import BancLecture


@tagged("post_install", "-at_install", "bf_expense_ocr")
class TestLangue(BancLecture):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_expense_ocr"], ["fr_CA"], overwrite=True)
        cls.expense_employee.user_id.lang = "fr_CA"

    def test_le_rattrapage_ecrit_dans_la_langue_de_l_employe(self):
        depense = self._depense_vierge()
        # Le rattrapage valide après chaque dépense ; un essai ne le peut pas.
        self.patch(self.env.cr, "commit", lambda: None)
        with self._passerelle(enveloppe={"data": None, "error": None}):
            # L'usager du cron travaille en anglais.
            self.env["hr.expense"].with_context(lang="en_US")._cron_ocr_batch()
        self.assertEqual(depense.ocr_state, "error")
        self.assertEqual(depense.ocr_error_message, "Lecture sans données")

    def test_langue_du_lecteur(self):
        depense = self._depense_vierge()
        self.assertEqual(depense._ocr_langue_lecteur(), "fr_CA")
        self.expense_employee.user_id.lang = "en_US"
        self.assertEqual(depense._ocr_langue_lecteur(), "en_US")

    def test_copie_recadree_nommee_dans_la_langue_de_l_appel(self):
        depense = self._depense_vierge()
        piece = depense._ocr_attachment()
        res = {"cropped_base64": piece.datas, "crop": {"ext": ".png", "mimetype": "image/png"}}
        copie = depense.with_context(lang="fr_CA")._ocr_poser_recadrage(piece, res)
        self.assertEqual(copie.name, "IMG_4821 (recadré).png")
        copie = depense.with_context(lang="en_US")._ocr_poser_recadrage(piece, res)
        self.assertEqual(copie.name, "IMG_4821 (cropped).png")
