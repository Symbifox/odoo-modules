import logging
import sys
import time

import odoo
from odoo import api, models, tools
from odoo.service import server as odoo_server
from odoo.tools.translate import code_translations

_logger = logging.getLogger(__name__)


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def _register_hook(self):
        super()._register_hook()
        if not self._bf_warm_start_wanted():
            return
        try:
            # A savepoint: a failed query must not leave the loading cursor aborted.
            with self.env.cr.savepoint():
                self._bf_warm_start()
        except Exception:
            # A warm-up must never stop a registry from loading.
            _logger.warning("bf_warm_start: warm-up skipped", exc_info=True)

    @api.model
    def _bf_warm_start_wanted(self):
        """Only in a process that serves HTTP requests.

        Only for the ``odoo server`` command: not in ``odoo shell`` or another
        command (they create a server object too), not during an install or
        upgrade run, not in tests, and not in the gevent process, which only
        carries websockets.
        """
        cfg = tools.config
        if getattr(sys.modules.get("odoo.cli"), "COMMAND", None) != "server":
            return False
        if cfg.get("stop_after_init") or cfg.get("init") or cfg.get("update"):
            return False
        if cfg.get("test_enable") or odoo.evented:
            return False
        return odoo_server.server is not None

    @api.model
    def _bf_warm_start_langs(self):
        """The languages of the people who open backend pages."""
        installed = {code for code, _name in self.env["res.lang"].get_installed()}
        self.env["res.users"].flush_model(["active", "share", "partner_id"])
        self.env["res.partner"].flush_model(["lang"])
        self.env.cr.execute(
            """
            SELECT DISTINCT p.lang
              FROM res_users u
              JOIN res_partner p ON p.id = u.partner_id
             WHERE u.active AND NOT u.share AND p.lang IS NOT NULL
            """
        )
        return sorted(lang for (lang,) in self.env.cr.fetchall() if lang in installed)

    @api.model
    def _bf_warm_start_modules(self):
        """The module list the web client sends (see web's ``session_info``)."""
        return list(self.pool._init_modules) + list(odoo.conf.server_wide_modules or [])

    @api.model
    def _bf_warm_start(self):
        """Fill the process-wide web translation cache, then the hash.

        ``code_translations`` lives for the whole process and is filled on
        demand, one .po file at a time. Filled here, in the prefork master when
        ``db_name`` is set, it is inherited by every worker forked afterwards.
        """
        modules = self._bf_warm_start_modules()
        for lang in self._bf_warm_start_langs():
            start = time.monotonic()
            for module in modules:
                code_translations.get_web_translations(module, lang)
            loaded = time.monotonic() - start
            # Same ormcache key as session_info: frozenset(modules), lang.
            self.env["ir.http"].sudo().get_web_translations_hash(modules, lang)
            _logger.info(
                "bf_warm_start: %s web translations loaded for %d modules in %.1f s "
                "(hash in %.1f s)",
                lang, len(modules), loaded, time.monotonic() - start - loaded,
            )
