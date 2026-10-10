"""The linked-reports window reads in the user's language.

Its title was a plain string in the returned action: once the source became
English, a French user would have read it in English.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCallArchiveLanguage(TransactionCase):

    def test_linked_reports_title_in_the_user_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        call = self.env["call.archive.call"].new({})
        self.assertEqual(
            call.with_context(lang="fr_CA").action_view_meeting_records()["name"],
            "Comptes rendus liés",
        )
        self.assertEqual(
            call.with_context(lang="en_US").action_view_meeting_records()["name"],
            "Linked meeting reports",
        )
