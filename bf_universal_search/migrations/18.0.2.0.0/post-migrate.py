"""Widen the search scope (CRM, rencontres, communications, finance, documents),
backfill the new config fields on the existing records, and pull credentials out
of the global search.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.bf_universal_search.hooks import (
    _SEARCH_CONFIGS,
    create_search_configs,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    IrModelData = env["ir.model.data"]

    created = create_search_configs(env)

    # Fields introduced in 18.0.2.0.0, with their unconfigured default. A config
    # that predates this release carries the default for each; we only fill a
    # field the admin hasn't already touched, so reordering (sequence), a custom
    # domain, etc. survive the upgrade. Pre-existing fields (search_fields, limit,
    # icon, category, sequence) are left exactly as the admin/1.4.0 install left
    # them — the new behaviour rides on top rather than resetting the scope.
    _NEW_FIELDS = {
        "detail_fields": False,
        "domain": False,
        "closed_domain": False,
        "order": False,
        "search_by_id": False,
        "min_length": 2,
    }

    updated = 0
    for spec in _SEARCH_CONFIGS:
        suffix = spec["suffix"]
        config = env.ref(
            f"bf_universal_search.search_config_{suffix}", raise_if_not_found=False
        )
        if not config or config.id <= 0:
            continue

        to_write = {}
        for field_name, unset in _NEW_FIELDS.items():
            spec_value = spec.get(field_name, unset)
            current = config[field_name]
            # Odoo stores an empty Char as False; normalise for the comparison.
            current_norm = current or False if unset is False else current
            if current_norm == unset and spec_value != current:
                to_write[field_name] = spec_value

        if to_write:
            config.write(to_write)
            updated += 1

    # Credentials were added by the 18.0.1.3.0 migration even though the install
    # hook had dropped them on purpose: credential names have no business in a
    # global search. Deactivate rather than delete, so the toggle stays reversible.
    cred_data = IrModelData.search([
        ("module", "=", "bf_universal_search"),
        ("name", "=", "search_config_credentials"),
    ], limit=1)
    if cred_data:
        cred = env["bf.universal.search.config"].browse(cred_data.res_id).exists()
        if cred and cred.active:
            cred.active = False
            _logger.info("Universal search: deactivated the credentials config")

    _logger.info(
        "Universal search 18.0.2.0.0: %d config(s) created, %d updated", created, updated
    )
