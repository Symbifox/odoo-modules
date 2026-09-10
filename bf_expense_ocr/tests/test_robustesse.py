# -*- coding: utf-8 -*-
"""Une passerelle muette ne doit jamais coûter sa photo à l'utilisateur."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BancLecture, PNG_1x1


@tagged("post_install", "-at_install", "bf_expense_ocr")
class TestRobustesse(BancLecture):

    def test_une_passerelle_non_configuree_ne_casse_rien(self):
        """`bf.llm` lève un UserError quand aucun fournisseur n'existe."""
        d = self._depense_vierge()
        with self._passerelle(leve=UserError("Aucun fournisseur LLM configuré")):
            self.assertFalse(d.action_ocr_scan())
        self.assertEqual(d.ocr_state, "error")
        self.assertIn("fournisseur", d.ocr_error_message)
        self.assertAlmostEqual(d.total_amount_currency, 0.0, places=2)

    def test_une_erreur_de_modele_revient_dans_l_enveloppe(self):
        d = self._depense_vierge()
        env_err = {"ok": False, "error": "rate limited", "data": None, "raw": {"x": 1}}
        with self._passerelle(enveloppe=env_err):
            self.assertFalse(d.action_ocr_scan())
        self.assertEqual(d.ocr_state, "error")
        self.assertEqual(d.ocr_error_message, "rate limited")

    def test_un_json_illisible_revient_en_erreur(self):
        d = self._depense_vierge()
        env_err = {"ok": False, "error": "LLM did not return valid JSON",
                   "data": None, "raw": {}}
        with self._passerelle(enveloppe=env_err):
            d.action_ocr_scan()
        self.assertEqual(d.ocr_state, "error")

    def test_sans_piece_jointe_on_le_dit(self):
        d = self._depense_vierge(avec_piece=False)
        with self._passerelle(self.RECU_JUSTE) as espion:
            self.assertFalse(d.action_ocr_scan())
        self.assertEqual(espion.appels, [], "rien ne part quand il n'y a rien à lire")
        self.assertEqual(d.ocr_state, "error")

    def test_une_piece_jointe_d_un_type_illisible_est_ignoree(self):
        d = self._depense_vierge(avec_piece=False)
        self.env["ir.attachment"].create({
            "name": "notes.txt", "res_model": "hr.expense", "res_id": d.id,
            "datas": PNG_1x1, "mimetype": "text/plain",
        })
        with self._passerelle(self.RECU_JUSTE) as espion:
            d.action_ocr_scan()
        self.assertEqual(espion.appels, [])
        self.assertEqual(d.ocr_state, "error")

    def test_la_lecture_automatique_avale_ses_erreurs(self):
        """Le téléversement ne doit pas échouer parce que la lecture échoue."""
        self.company_data["company"].expense_ocr_auto = True
        d = self._depense_vierge(avec_piece=False)
        piece = self.env["ir.attachment"].create({
            "name": "recu.png", "res_model": "hr.expense", "res_id": d.id,
            "datas": PNG_1x1, "mimetype": "image/png",
        })
        with self._passerelle(leve=ValueError("la passerelle explose")):
            d.attach_document(attachment_ids=[piece.id])  # ne doit pas lever
        self.assertEqual(d.message_main_attachment_id, piece,
                         "la photo est restée attachée")

    def test_une_depense_deja_comptabilisee_ne_se_relit_pas(self):
        d = self._depense_vierge()
        with self._passerelle(self.RECU_JUSTE):
            d.action_ocr_scan()
        feuille = self._rapport(d)
        self.assertEqual(feuille.state, "post")
        with self._passerelle(self.RECU_JUSTE) as espion:
            self.assertFalse(d.action_ocr_scan())
        self.assertEqual(espion.appels, [])
