# -*- coding: utf-8 -*-
"""The notepad's action titles and notifications follow the user's language.

They were plain French strings in Python, outside any translation.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNotepadLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_bloc_notes"], ["fr_CA"], overwrite=True)
        cls.note = cls.env["bf.note"].create({"name": "Language note", "body": "<p>x</p>"})

    def test_action_titles_follow_the_language(self):
        self.assertEqual(self.note.with_context(lang="en_US").action_open_task_wizard()["name"],
                         "Convert to task")
        self.assertEqual(self.note.with_context(lang="fr_CA").action_open_task_wizard()["name"],
                         "Convertir en tâche")
        self.assertEqual(self.note.with_context(lang="fr_CA").action_open_reroute_wizard()["name"],
                         "Re-router la note")
