# -*- coding: utf-8 -*-
"""Result group labels exist in every installed language.

The configs are created by the install hook, which runs AFTER Odoo has loaded
the module's catalogues: a label left to the catalogue stayed in the source
language for every French-speaking user of a fresh install.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_universal_search.hooks import _SEARCH_CONFIGS, create_search_configs


@tagged("post_install", "-at_install")
class TestSearchLabelLanguage(TransactionCase):

    def test_a_created_config_carries_the_french_label(self):
        if "project.task" not in self.env:
            self.skipTest("project is not installed")
        self.env["res.lang"]._activate_lang("fr_CA")
        taches = [spec for spec in _SEARCH_CONFIGS if spec["suffix"] == "tasks"]
        existant = self.env.ref("bf_universal_search.search_config_tasks",
                                raise_if_not_found=False)
        if existant:
            self.env["ir.model.data"].search([
                ("module", "=", "bf_universal_search"), ("name", "=", "search_config_tasks"),
            ]).unlink()
            existant.unlink()
        self.assertEqual(create_search_configs(self.env, taches), 1)
        config = self.env.ref("bf_universal_search.search_config_tasks")
        self.assertEqual(config.with_context(lang="en_US").name, "Tasks")
        self.assertEqual(config.with_context(lang="fr_CA").name, "Tâches")
