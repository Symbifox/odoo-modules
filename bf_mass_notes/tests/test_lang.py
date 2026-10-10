"""The mass-note Action reads in the user's language.

The install hook creates the Action on every thread model AFTER Odoo has
loaded the catalogues: no catalogue ever reaches those records, so the hook
writes each installed language itself. The wizard titles its window through
the code catalogue, since the server action code is never translated.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_mass_notes.hooks import ACTION_CODE_MARKER, _name_translations, post_init_hook


@tagged("post_install", "-at_install")
class TestMassNoteLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")

    def _action_on(self, model_name):
        return self.env["ir.actions.server"].search([
            ("binding_model_id.model", "=", model_name),
            ("state", "=", "code"),
            ("code", "like", ACTION_CODE_MARKER),
        ])

    def test_hook_writes_every_installed_language(self):
        self._action_on("res.partner").unlink()
        post_init_hook(self.env)
        action = self._action_on("res.partner")
        self.assertEqual(len(action), 1)
        self.assertEqual(action.with_context(lang="en_US").name, "Add a note (in bulk)")
        self.assertEqual(action.with_context(lang="fr_CA").name, "Ajouter une note (en lot)")

    def test_names_come_from_the_code_catalogue(self):
        self.assertEqual(_name_translations(self.env)["fr_CA"], "Ajouter une note (en lot)")

    def test_wizard_window_title_in_the_user_language(self):
        wizard = self.env["bf.mass.note.wizard"]
        partners = self.env["res.partner"].search([], limit=2)
        action = wizard.with_context(lang="fr_CA").action_open_for("res.partner", partners.ids)
        self.assertEqual(action["name"], "Ajouter une note en lot")
        self.assertEqual(action["context"]["active_ids"], partners.ids)
        action = wizard.with_context(lang="en_US").action_open_for("res.partner", partners.ids)
        self.assertEqual(action["name"], "Add a note in bulk")
