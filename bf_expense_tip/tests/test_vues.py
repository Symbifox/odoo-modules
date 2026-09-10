# -*- coding: utf-8 -*-
"""Les vues que l'utilisateur ouvre vraiment.

⚠️ « Module chargé » ne dit rien du rendu, et un héritage qui se greffe sur la
mauvaise vue s'installe sans broncher. Ces contrôles demandent à Odoo la vue
qu'il SERVIRAIT, puis y cherchent le champ.
"""

from odoo.tests import tagged

from .common import BancPourboire


@tagged("post_install", "-at_install", "bf_expense_tip")
class TestVues(BancPourboire):

    def test_le_champ_est_sur_le_formulaire_servi(self):
        vues = self.env["hr.expense"].get_views([(False, "form")])
        self.assertIn('name="tip_amount_currency"', vues["views"]["form"]["arch"])

    def test_la_colonne_est_sur_la_LISTE_SERVIE(self):
        """🔴 Le piège qui a coûté un redéploiement.

        `hr_expense.view_expenses_tree` n'est PAS la liste par défaut : c'est
        une vue primaire qui hérite de
        `hr_expense_view_expenses_analysis_tree` et n'ajoute qu'un `js_class`.
        Un `position="after"` posé dessus s'installe très bien et n'apparaît
        nulle part. Le contrôle qui tranche demande la vue servie.
        """
        vues = self.env["hr.expense"].get_views([(False, "list")])
        self.assertIn('name="tip_amount_currency"', vues["views"]["list"]["arch"])

    def test_le_reglage_est_sur_la_page_des_reglages(self):
        vues = self.env["res.config.settings"].get_views([(False, "form")])
        self.assertIn('name="expense_tip_account_id"', vues["views"]["form"]["arch"])
