# -*- coding: utf-8 -*-
"""Un pont muet ne doit jamais coûter sa photo à l'utilisateur."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BancLecture, PNG_1x1


@tagged("post_install", "-at_install", "bf_expense_ocr")
class TestRobustesse(BancLecture):

    def test_un_pont_injoignable_ne_casse_rien(self):
        """La socket absente remonte en FileNotFoundError depuis le transport."""
        d = self._depense_vierge()
        with self._passerelle(leve=FileNotFoundError("/run/claude-bridge/bridge.sock")):
            self.assertFalse(d.action_ocr_scan())
        self.assertEqual(d.ocr_state, "error")
        self.assertIn("bridge.sock", d.ocr_error_message)
        self.assertAlmostEqual(d.total_amount_currency, 0.0, places=2)

    def test_un_locataire_non_declare_ne_casse_rien(self):
        """`bf.ai.bridge.tenant()` lève plutôt que de deviner — et c'est voulu.

        Un défaut codé en dur enverrait le reçu sur l'abonnement d'un autre
        client. L'appel doit échouer, pas réussir ailleurs.
        """
        d = self._depense_vierge()
        with self._passerelle(leve=UserError("Le locataire n'est pas déclaré")):
            self.assertFalse(d.action_ocr_scan())
        self.assertEqual(d.ocr_state, "error")
        self.assertIn("locataire", d.ocr_error_message)

    def test_une_erreur_de_lecture_revient_dans_l_enveloppe(self):
        d = self._depense_vierge()
        with self._passerelle(enveloppe={"data": None, "error": "Receipt scan timed out (90s)"}):
            self.assertFalse(d.action_ocr_scan())
        self.assertEqual(d.ocr_state, "error")
        self.assertEqual(d.ocr_error_message, "Receipt scan timed out (90s)")

    def test_une_enveloppe_vide_revient_en_erreur(self):
        d = self._depense_vierge()
        with self._passerelle(enveloppe={"data": None, "error": None}):
            self.assertFalse(d.action_ocr_scan())
        self.assertEqual(d.ocr_state, "error")

    def test_une_reponse_qui_n_est_pas_un_dict_revient_en_erreur(self):
        """Le transport rend ce que le service a écrit ; il peut mentir."""
        d = self._depense_vierge()
        with self._passerelle(enveloppe="pas du JSON"):
            self.assertFalse(d.action_ocr_scan())
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
        with self._passerelle(leve=ValueError("le pont explose")):
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
