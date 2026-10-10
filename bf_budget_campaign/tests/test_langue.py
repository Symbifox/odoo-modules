# -*- coding: utf-8 -*-
"""Le plan analytique des campagnes porte son nom dans chaque langue installée.

Il est partagé par toute la société : son nom ne doit pas dépendre de la langue
de qui a créé, le premier, le compte d'une campagne.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_budget_campaign.models.campagne import PARAM_PLAN


@tagged("post_install", "-at_install")
class TestLangueDuPlan(TransactionCase):

    def test_le_plan_porte_chaque_langue(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["res.lang"]._activate_lang("en_US")
        # Forcer la création : la base copiée peut déjà avoir son plan.
        self.env["ir.config_parameter"].sudo().set_param(PARAM_PLAN, "0")
        campagne = self.env["utm.campaign"].with_context(lang="fr_CA").create(
            {"name": "Campagne de langue"})
        campagne.action_bf_create_analytic_account()
        plan = campagne.analytic_account_id.plan_id
        self.assertEqual(plan.with_context(lang="fr_CA").name, "Campagnes")
        self.assertEqual(plan.with_context(lang="en_US").name, "Campaigns")
