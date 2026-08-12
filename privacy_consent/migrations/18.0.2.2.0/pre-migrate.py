"""Pre-migration: Register manually-created purposes in ir_model_data.

The user created 3 purposes manually in the UI (agent, sensibles, training)
before the data files had them. We register them in ir_model_data with
noupdate=False so the data file update overwrites them with rich content
(descriptions, notices). After update, noupdate will be set to True.
"""
import logging

_logger = logging.getLogger(__name__)

PURPOSE_MAP = {
    # (db_code, xml_id)
    "agent": "purpose_software_install",
    "sensibles": "purpose_high_risk",
    "training": "purpose_training",
}

NOTICE_XMLIDS_TO_ADD = [
    "notice_software_install",
    "notice_high_risk",
    "notice_training",
]


def migrate(cr, version):
    if not version:
        return

    _logger.info("privacy_consent: registering manual purposes in ir_model_data")

    # Register manually-created purposes so the data file can manage them
    for code, xmlid in PURPOSE_MAP.items():
        cr.execute(
            """
            INSERT INTO ir_model_data (name, module, model, res_id, noupdate)
            SELECT %(xmlid)s, 'privacy_consent', 'privacy.purpose', p.id, FALSE
            FROM privacy_purpose p
            WHERE p.code = %(code)s
            AND NOT EXISTS (
                SELECT 1 FROM ir_model_data imd
                WHERE imd.module = 'privacy_consent' AND imd.name = %(xmlid)s
            )
            """,
            {"xmlid": xmlid, "code": code},
        )
        if cr.rowcount:
            _logger.info(
                "  Registered purpose code=%s as XML ID %s", code, xmlid
            )

    # Also update the existing purposes (marketing, recording) whose names
    # changed in the data files — clear noupdate so the data file can
    # update them with the new names and enriched descriptions
    for xmlid in ("purpose_marketing", "purpose_recording"):
        cr.execute(
            """
            UPDATE ir_model_data SET noupdate = FALSE
            WHERE module = 'privacy_consent' AND name = %s
            """,
            (xmlid,),
        )

    # Same for notices — allow update of existing notice names (F-XX prefixes)
    cr.execute(
        """
        UPDATE ir_model_data SET noupdate = FALSE
        WHERE module = 'privacy_consent'
        AND model = 'privacy.notice'
        """
    )

    # Also clear noupdate on the mail template so the Jinja fix gets applied
    cr.execute(
        """
        UPDATE ir_model_data SET noupdate = FALSE
        WHERE module = 'privacy_consent'
        AND name = 'mail_template_consent_request'
        """
    )
