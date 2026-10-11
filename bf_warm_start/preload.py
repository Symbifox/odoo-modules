"""Préchargement du registre par le processus maître, sans `db_name`.

Avec `db_name` dans odoo.conf, Odoo précharge la base avant de créer les workers, mais
il la CRÉE aussi si elle est absente au démarrage (odoo/cli/server.py), et y installe
`base` avec admin/admin : pendant une restauration, une base vide serait servie à
l'adresse de la production. On obtient le même préchargement autrement : chargé comme
module « server-wide », ce fichier remplace `odoo.service.server.preload_registries`.
Quand le maître préfork l'appelle sans base, il lui donne la seule base existante que
désigne `dbfilter`. Il ne crée jamais rien.
"""
import logging
import sys

import odoo
from odoo.service import db as odoo_db
from odoo.service import server as odoo_server
from odoo.tools import config

_logger = logging.getLogger(__name__)
_original = None


def _serving_command():
    """La commande `odoo server` (ou sans commande), hors montée et hors tests."""
    if getattr(sys.modules.get("odoo.cli"), "COMMAND", None) != "server":
        return False
    if odoo.evented:
        return False
    if config.get("stop_after_init") or config.get("init") or config.get("update"):
        return False
    return not (config.get("test_enable") or config.get("test_file"))


def _dbnames_to_preload():
    """La seule base existante que dbfilter laisse passer, sinon rien."""
    if not config.get("workers") or not _serving_command():
        return []
    if not config.get("dbfilter"):
        return []
    try:
        existing = odoo_db.list_dbs(force=True)
        # host="" : hors requête, db_filter lirait request.httprequest et lèverait
        # (le préchargement serait alors sauté sans bruit). Aucun %h ni %d ici.
        kept = odoo.http.db_filter(existing, host="")
    except Exception:  # pas de serveur de base joignable : on ne précharge rien
        _logger.warning("bf_warm_start: databases not listed, no preload", exc_info=True)
        return []
    return list(kept) if len(kept) == 1 else []


def _preload_registries(dbnames):
    if not dbnames:
        dbnames = _dbnames_to_preload()
        if dbnames:
            _logger.info("bf_warm_start: preloading %s before forking the workers", dbnames[0])
    return _original(dbnames)


def post_load():
    global _original
    if _original is None:
        _original = odoo_server.preload_registries
        odoo_server.preload_registries = _preload_registries
