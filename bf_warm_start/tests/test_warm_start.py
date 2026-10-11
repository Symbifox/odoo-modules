import sys
from unittest.mock import patch

import odoo
from odoo.service import server as odoo_server
from odoo.tests import TransactionCase, tagged
from odoo.tools import config
from odoo.tools.translate import code_translations

from odoo.addons.bf_warm_start import preload

SERVING = {"stop_after_init": False, "init": {}, "update": {}, "test_enable": False,
           "test_file": False, "workers": 4, "dbfilter": "^prod$"}


@tagged("post_install", "-at_install")
class TestWarmStart(TransactionCase):
    def setUp(self):
        super().setUp()
        self.IrHttp = self.env["ir.http"]

    def _context(self, command="server", evented=False, server=object(), **cfg):
        cli = sys.modules.setdefault("odoo.cli", type(sys)("odoo.cli"))
        return (patch.dict(config.options, dict(SERVING, **cfg)),
                patch.object(cli, "COMMAND", command, create=True),
                patch.object(odoo, "evented", evented),
                patch.object(odoo_server, "server", server))

    def _wanted(self, **kw):
        a, b, c, d = self._context(**kw)
        with a, b, c, d:
            return self.IrHttp._bf_warm_start_wanted()

    def test_wanted_only_for_the_server_command(self):
        self.assertTrue(self._wanted())
        self.assertFalse(self._wanted(command="shell"), "odoo shell builds a server object too")
        self.assertFalse(self._wanted(command="gevent"))
        self.assertFalse(self._wanted(evented=True))
        self.assertFalse(self._wanted(update={"web": 1}), "-u run")
        self.assertFalse(self._wanted(init={"web": 1}), "-i run")
        self.assertFalse(self._wanted(stop_after_init=True))
        self.assertFalse(self._wanted(test_enable=True))
        # The real test process: test_enable is on.
        self.assertFalse(self.IrHttp._bf_warm_start_wanted())

    def _preload(self, existing, kept, **kw):
        a, b, c, d = self._context(**kw)
        with a, b, c, d, \
                patch.object(preload.odoo_db, "list_dbs", return_value=existing) as listed, \
                patch.object(odoo.http, "db_filter", return_value=kept):
            return preload._dbnames_to_preload(), listed.called

    def test_preload_picks_the_single_database_of_dbfilter(self):
        self.assertEqual(self._preload(["prod", "autre"], ["prod"])[0], ["prod"])

    def test_preload_never_guesses(self):
        self.assertEqual(self._preload(["a", "b"], ["a", "b"])[0], [], "two candidates")
        self.assertEqual(self._preload([], [])[0], [], "missing database: nothing, never created")
        self.assertEqual(self._preload(["prod"], ["prod"], dbfilter="")[0], [])

    def test_preload_uses_the_real_db_filter_outside_a_request(self):
        """No mock of db_filter: outside a request it must not need one."""
        a, b, c, d = self._context(dbfilter="^prod$")
        with a, b, c, d, patch.object(preload.odoo_db, "list_dbs", return_value=["prod", "autre"]):
            self.assertEqual(preload._dbnames_to_preload(), ["prod"])

    def test_preload_only_in_prefork_server(self):
        dbs, listed = self._preload(["prod"], ["prod"], workers=0)
        self.assertEqual(dbs, [])
        self.assertFalse(listed, "threaded mode: not even listed")
        self.assertEqual(self._preload(["prod"], ["prod"], command="shell")[0], [])
        self.assertEqual(self._preload(["prod"], ["prod"], command="gevent")[0], [])
        self.assertEqual(self._preload(["prod"], ["prod"], update={"web": 1})[0], [])

    def test_explicit_databases_are_kept(self):
        seen = []
        with patch.object(preload, "_original", side_effect=lambda dbs: seen.append(dbs)), \
                patch.object(preload, "_dbnames_to_preload", return_value=["prod"]):
            preload._preload_registries(["given"])
            preload._preload_registries([])
        self.assertEqual(seen, [["given"], ["prod"]])

    def test_langs_are_installed_internal_langs(self):
        admin = self.env.ref("base.user_admin")
        self.env["res.lang"]._activate_lang("fr_FR")
        admin.partner_id.lang = "fr_FR"
        langs = self.IrHttp._bf_warm_start_langs()
        self.assertIn("fr_FR", langs)
        installed = {code for code, _ in self.env["res.lang"].get_installed()}
        self.assertTrue(set(langs) <= installed)

    def test_warm_fills_the_process_cache_and_the_session_info_hash(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        self.env.ref("base.user_admin").partner_id.lang = "fr_FR"
        modules = self.IrHttp._bf_warm_start_modules()
        self.assertIn("web", modules)
        for module in modules:
            code_translations.web_translations.pop((module, "fr_FR"), None)
        self.env.registry.clear_cache()
        self.IrHttp._bf_warm_start()
        missing = [m for m in modules if (m, "fr_FR") not in code_translations.web_translations]
        self.assertFalse(missing, "web translations not loaded for %s" % missing[:5])
        # Same key as web's session_info: server-wide modules after the registry's.
        session_modules = list(self.env.registry._init_modules) + list(odoo.conf.server_wide_modules or [])
        with patch.object(type(self.IrHttp), "get_translations_for_webclient") as rebuilt:
            self.IrHttp.sudo().get_web_translations_hash(session_modules, "fr_FR")
        self.assertFalse(rebuilt.called, "the hash session_info asks for was not cached")

    def test_warm_up_never_raises(self):
        cls = type(self.IrHttp)
        with patch.object(cls, "_bf_warm_start_wanted", return_value=True), \
                patch.object(cls, "_bf_warm_start", side_effect=RuntimeError("boom")):
            self.IrHttp._register_hook()
