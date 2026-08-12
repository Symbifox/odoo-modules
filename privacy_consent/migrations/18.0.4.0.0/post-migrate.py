"""Post-migration for 18.0.4.0.0 — multi-framework privacy support.

Wires every existing privacy record to the Loi 25 framework so behaviour is
unchanged for current installs:
- sets each company's default framework to Loi 25 (where unset),
- backfills framework_id = Loi 25 on all framework-aware tables via RAW SQL, so
  the immutable destruction register's write() override and SHA-256 hash chain
  are never triggered,
- re-verifies the destruction-register hash chain is intact.

NOTE: the consent-request and granted-confirmation mail templates are
``noupdate="1"`` and are intentionally NOT rewritten here, to avoid clobbering
any in-body customisation (e.g. the privacy contact e-mail). Existing Loi 25
installs keep their current, correct e-mail bodies; fresh installs receive the
new framework-parameterised templates. To make e-mails framework-variable on an
existing install, reset those two templates to their module defaults.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_FRAMEWORK_TABLES = (
    "privacy_consent",
    "privacy_notice",
    "privacy_retention_calendar",
    "privacy_anonymization_assessment",
    "privacy_purpose",
    "privacy_destruction_request",
    "privacy_destruction_register",
)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    framework = env.ref("privacy_consent.framework_loi25", raise_if_not_found=False)
    if not framework:
        _logger.error(
            "privacy_consent 18.0.4.0.0: framework_loi25 introuvable; "
            "rétro-remplissage ignoré."
        )
        return
    fw_id = framework.id

    # 1. Default framework on every company that lacks one.
    cr.execute(
        "UPDATE res_company SET default_privacy_framework_id = %s "
        "WHERE default_privacy_framework_id IS NULL",
        (fw_id,),
    )
    _logger.info("Cadre par défaut posé sur %s société(s)", cr.rowcount)

    # 2. Backfill framework_id on every framework-aware table.
    #    Raw SQL: never triggers the register write() override nor recomputes any
    #    hash (the verification hash does not include framework_id).
    for table in _FRAMEWORK_TABLES:
        cr.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = 'framework_id'",
            (table,),
        )
        if not cr.fetchone():
            _logger.warning("Colonne framework_id absente de %s; ignorée", table)
            continue
        cr.execute(
            "UPDATE %s SET framework_id = %%s WHERE framework_id IS NULL" % table,
            (fw_id,),
        )
        _logger.info(
            "framework_id rétro-rempli sur %s ligne(s) de %s", cr.rowcount, table
        )

    # 3. Re-verify the destruction-register hash chain is intact (expected: 0).
    try:
        broken = env["privacy.destruction.register"].cron_verify_chain_integrity()
        if broken:
            _logger.error(
                "Intégrité du registre de destruction: %s entrée(s) incohérente(s) "
                "APRÈS migration 18.0.4.0.0 — vérification manuelle requise.",
                broken,
            )
        else:
            _logger.info(
                "Chaîne d'intégrité du registre de destruction intacte (0 anomalie)."
            )
    except Exception:  # pragma: no cover - defensive, never block the upgrade
        _logger.exception("Échec de la vérification d'intégrité du registre")
