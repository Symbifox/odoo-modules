# -*- coding: utf-8 -*-
"""The timer speaks the user's language.

Its error messages were plain French strings, outside any translation: an
English-speaking user read them in French. They are now source strings in
English with their French in the catalogue.
"""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTimerLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_timesheet_timer"], ["fr_CA"], overwrite=True)

    def test_an_error_reads_in_the_user_language(self):
        Timer = self.env["bf.timer"]
        with self.assertRaisesRegex(UserError, "Task not found"):
            Timer.with_context(lang="en_US").start_timer(0)
        with self.assertRaisesRegex(UserError, "Tâche introuvable"):
            Timer.with_context(lang="fr_CA").start_timer(0)
