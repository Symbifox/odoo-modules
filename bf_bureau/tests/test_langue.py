# -*- coding: utf-8 -*-
"""A pane's validation messages read in the user's language.

They were f-strings in French, outside any translation.
"""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeskLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(["bf_bureau"], ["fr_CA"], overwrite=True)

    def _pane(self, lang):
        action = self.env.ref("base.action_res_users")
        desk = self.env["bf.bureau.desk"].create({"name": "Language desk", "layout": "single"})
        return self.env["bf.bureau.pane"].with_context(lang=lang).create({
            "desk_id": desk.id, "slot": "full", "action_id": action.id,
            "view_type": "list", "domain_override": "{'not': 'a list'}",
        })

    def test_an_invalid_domain_is_refused_in_the_user_language(self):
        with self.assertRaisesRegex(ValidationError, "it must be a Python list"):
            self._pane("en_US")
        with self.assertRaisesRegex(ValidationError, "doit être une liste Python"):
            self._pane("fr_CA")
