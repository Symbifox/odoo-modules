# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""The "Reorder this chatter by date" actions read in each user's language.

The two that ``post_init_hook`` creates for meetings never see the catalogue
(a hook runs after it is loaded), so the hook names them in every installed
language itself, and 18.0.4.2.0 switches the ones still carrying the French
name that was delivered before.
"""
import importlib.util

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_chatter_chronological import hooks

FR = "Réordonner ce chatter par date"
EN = "Reorder this chatter by date"


@tagged("post_install", "-at_install")
class TestChronologicalLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_chatter_chronological"], ["fr_CA"], overwrite=True)

    def _sans_reunions(self):
        if not any(m in self.env for m in hooks.LIAISONS_OPTIONNELLES):
            self.skipTest("no meeting model on this database")

    def _nom(self, action, lang):
        self.env.cr.execute("SELECT name->>%s FROM ir_act_server WHERE id = %s", (lang, action.id))
        return self.env.cr.fetchone()[0]

    def test_xml_action_reads_in_french(self):
        action = self.env.ref("bf_chatter_chronological.action_chatter_chrono_project_task")
        self.assertEqual(self._nom(action, "en_US"), EN)
        self.assertEqual(self._nom(action, "fr_CA"), FR)

    def test_hook_names_its_actions_in_every_language(self):
        self._sans_reunions()
        hooks.liaisons_posees(self.env).unlink()
        self.assertTrue(hooks._ensure_optional_bindings(self.env))
        actions = hooks.liaisons_posees(self.env)
        self.assertTrue(actions)
        for action in actions:
            self.assertEqual(self._nom(action, "en_US"), EN)
            self.assertEqual(self._nom(action, "fr_CA"), FR)

    def test_migration_switches_only_the_delivered_name(self):
        self._sans_reunions()
        hooks.liaisons_posees(self.env).unlink()
        hooks._ensure_optional_bindings(self.env)
        modele = hooks.liaisons_posees(self.env)[0]
        Action = self.env["ir.actions.server"].sudo()
        champs = {"model_id": modele.model_id.id, "binding_model_id": modele.binding_model_id.id,
                  "binding_view_types": "form", "state": "code", "code": hooks.CODE}
        retouchee = Action.create(dict(champs, name="x"))
        retouchee_fr = Action.create(dict(champs, name="x"))
        livree = modele
        # As 18.0.4.1.0 left them: the French in en_US. One renamed by hand, one
        # still delivered in en_US but renamed in French (a database where en_US is active).
        for action, valeur in ((livree, {"en_US": FR}), (retouchee, {"en_US": "Tri maison"}),
                               (retouchee_fr, {"en_US": FR, "fr_CA": "Tri maison FR"})):
            self.env.cr.execute("UPDATE ir_act_server SET name = %s::jsonb WHERE id = %s",
                                (__import__("json").dumps(valeur), action.id))
        self.env.invalidate_all()
        chemin = get_module_path("bf_chatter_chronological") + "/migrations/18.0.4.2.0/end-migrate.py"
        spec = importlib.util.spec_from_file_location("bf_chrono_migration", chemin)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.migrate(self.env.cr, "18.0.4.1.0")
        self.env.invalidate_all()
        self.assertEqual(self._nom(livree, "en_US"), EN)
        self.assertEqual(self._nom(livree, "fr_CA"), FR)
        self.assertEqual(self._nom(retouchee, "en_US"), "Tri maison")
        self.assertIsNone(self._nom(retouchee, "fr_CA"))
        self.assertEqual(self._nom(retouchee_fr, "en_US"), EN)
        self.assertEqual(self._nom(retouchee_fr, "fr_CA"), "Tri maison FR")
