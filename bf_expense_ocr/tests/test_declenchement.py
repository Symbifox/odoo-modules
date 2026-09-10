# -*- coding: utf-8 -*-
"""Rien ne part sans qu'on l'ait décidé.

Un reçu de repas nomme un commerçant, une date et une heure. Ces contrôles
existent pour que l'envoi reste un geste, pas un défaut de configuration.
"""

from odoo.tests import tagged

from .common import BancLecture, PNG_1x1


@tagged("post_install", "-at_install", "bf_expense_ocr")
class TestDeclenchement(BancLecture):

    def test_par_defaut_la_lecture_automatique_est_eteinte(self):
        self.assertFalse(self.company_data["company"].expense_ocr_auto)

    def test_par_defaut_le_cron_est_eteint(self):
        cron = self.env.ref("bf_expense_ocr.ir_cron_expense_ocr_batch")
        self.assertFalse(cron.active, "un cron qui envoie des photos ne s'allume pas seul")

    def test_eteinte_une_piece_jointe_ne_declenche_rien(self):
        d = self._depense_vierge(avec_piece=False)
        piece = self.env["ir.attachment"].create({
            "name": "recu.png", "res_model": "hr.expense", "res_id": d.id,
            "datas": PNG_1x1, "mimetype": "image/png",
        })
        with self._passerelle(self.RECU_JUSTE) as espion:
            d.attach_document(attachment_ids=[piece.id])
        self.assertEqual(espion.appels, [], "aucun appel à la passerelle")
        self.assertEqual(d.ocr_state, "none")

    def test_allumee_une_piece_jointe_declenche_la_lecture(self):
        self.company_data["company"].expense_ocr_auto = True
        d = self._depense_vierge(avec_piece=False)
        piece = self.env["ir.attachment"].create({
            "name": "recu.png", "res_model": "hr.expense", "res_id": d.id,
            "datas": PNG_1x1, "mimetype": "image/png",
        })
        with self._passerelle(self.RECU_JUSTE) as espion:
            d.attach_document(attachment_ids=[piece.id])
        self.assertEqual(len(espion.appels), 1)
        self.assertEqual(d.ocr_state, "done")

    def test_le_bouton_marche_meme_quand_l_automatique_est_eteint(self):
        """L'acte explicite est toujours possible : c'est le point."""
        self.assertFalse(self.company_data["company"].expense_ocr_auto)
        d = self._depense_vierge()
        with self._passerelle(self.RECU_JUSTE) as espion:
            d.action_ocr_scan()
        self.assertEqual(len(espion.appels), 1)
        self.assertEqual(d.ocr_state, "done")

    def test_la_photo_qui_cree_la_depense(self):
        """Le geste du téléphone : téléverser une photo crée la dépense."""
        self.env.company.expense_ocr_auto = True
        self.company_data["company"].expense_ocr_auto = True
        # ⚠️ La pièce jointe appartient à celui qui téléverse. Créée par
        # l'admin, l'employé n'a pas le droit d'y écrire et le noyau lève un
        # AccessError en la rattachant — un faux échec qui ne dit rien du
        # module.
        employe = self.expense_user_employee
        piece = self.env["ir.attachment"].with_user(employe).create({
            "name": "IMG_9001.png", "res_model": "hr.expense",
            "datas": PNG_1x1, "mimetype": "image/png",
        })
        with self._passerelle(self.RECU_JUSTE) as espion:
            # ⚠️ Comme l'employé : `create_expense_from_attachments` prend
            # l'employé de `env.user`, et l'utilisateur comptable du banc n'en
            # a pas — l'expense sort alors avec `employee_id` nul.
            action = self.env["hr.expense"].with_user(
                employe).create_expense_from_attachments(
                    attachment_ids=[piece.id])
        ids = action["domain"][0][2]
        depense = self.env["hr.expense"].browse(ids)
        self.assertEqual(len(depense), 1)
        self.assertEqual(len(espion.appels), 1)
        self.assertEqual(depense.ocr_state, "done")
        self.assertAlmostEqual(depense.total_amount_currency, 26.30, places=2)

    # ---- le rattrapage ----

    def test_le_rattrapage_ne_prend_que_le_brouillon_jamais_lu_avec_piece(self):
        avec = self._depense_vierge(nom="avec piece")
        sans = self._depense_vierge(nom="sans piece", avec_piece=False)
        deja = self._depense_vierge(nom="deja lue")
        deja.ocr_state = "done"

        with self._passerelle(self.RECU_JUSTE) as espion:
            # ⚠️ Le cron commite ; en test on l'appelle sur un sous-ensemble
            # explicite pour ne pas dépendre des autres dépenses du banc.
            a_lire = self.env["hr.expense"].search([
                ("id", "in", (avec + sans + deja).ids),
                ("state", "=", "draft"), ("ocr_state", "=", "none"),
            ]).filtered(lambda d: d._ocr_attachment())
            for d in a_lire:
                d.action_ocr_scan()

        self.assertEqual(a_lire, avec)
        self.assertEqual(len(espion.appels), 1)
        self.assertEqual(sans.ocr_state, "none")
        self.assertEqual(deja.ocr_state, "done")
