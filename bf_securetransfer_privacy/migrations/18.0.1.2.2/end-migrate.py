"""18.0.1.2.2, `end` step: the catalogue is reloaded, but only where it can add
the French of a source term the `post` step put back — and nowhere else.

A translated term is keyed by its source text. While the source term of the
purpose summary held French (written there by 18.0.1.2.1), it matched no key of
`i18n/fr_CA.po` and no translation existed at all: both readers got French, and
the catalogue was never used. The `post` step puts the English source back, and
the catalogue then has keys to bind to.

🔴 THE RELOAD IS GATED, and that is not a nicety. `overwrite=True` does NOT
spare an adapted translation of a field translated TERM BY TERM: measured at
the bench, an organisation's summary carrying one paragraph more or less than
the source is REPLACED by the catalogue's French. The sentence written in the
18.0.1.2.0 and 18.0.1.2.1 migrations — "the catalogue never replaces an
existing key of a `noupdate` record" — holds for a field translated in one
block, such as the purpose's name (a `Char`), and not for this one.
`sources_awaiting_catalogue` therefore names only the fields whose source is
the delivered English AND which nobody has translated: there, the reload can
add something and cannot destroy anything. Everywhere else nothing is reloaded.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.bf_securetransfer_privacy.hooks import (
    MODULE,
    sources_awaiting_catalogue,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    waiting = sources_awaiting_catalogue(env)
    if not waiting:
        _logger.info("%s: no source term waits for the catalogue, nothing reloaded", MODULE)
        return
    langs = [code for code, _name in env["res.lang"].get_installed() if code != "en_US"]
    if not langs:
        return
    env["ir.module.module"]._load_module_terms([MODULE], langs, overwrite=True)
    _logger.info("%s: catalogue reloaded for %s, to carry the French of %s",
                 MODULE, ", ".join(langs), ", ".join(waiting))
