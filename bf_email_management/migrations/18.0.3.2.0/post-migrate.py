"""18.0.3.2.0: drop the bf_*_partner_ids fields, view, and m2m tables.

The 18.0.3.0.0 release added three Many2many fields on mail.compose.message
(bf_to_partner_ids, bf_cc_partner_ids, bf_bcc_partner_ids) plus a view
override (xml_id bf_email_compose_message_wizard_form). 18.0.3.2.0 drops all
of that in favor of the partner_cc_ids / partner_bcc_ids fields already
provided by mail_composer_cc_bcc.

Odoo's regular -u bf_email_management run will:
  * Remove ir.model.data rows whose external IDs are no longer in the
    manifest (the override view).
  * Drop the dangling ir.ui.view record for the override.

But the m2m relation tables on a TransientModel are NOT cleaned automatically
by the registry, and the ir.model.fields rows for the dropped fields will
linger as well. We sweep them here.
"""

import logging

_logger = logging.getLogger(__name__)

_M2M_TABLES = (
    "bf_compose_to_partner_rel",
    "bf_compose_cc_partner_rel",
    "bf_compose_bcc_partner_rel",
)
_DROPPED_FIELDS = (
    "bf_email_split_recipients",
    "bf_to_partner_ids",
    "bf_cc_partner_ids",
    "bf_bcc_partner_ids",
)
_DROPPED_VIEW_XMLIDS = (
    "bf_email_management.bf_email_compose_message_wizard_form",
)


def migrate(cr, version):
    if not version:
        return

    for table in _M2M_TABLES:
        cr.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table,),
        )
        if cr.fetchone():
            cr.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
            _logger.info("Dropped legacy m2m table %s", table)

    cr.execute(
        """
        DELETE FROM ir_model_fields
         WHERE model = 'mail.compose.message'
           AND name IN %s
        """,
        (_DROPPED_FIELDS,),
    )
    if cr.rowcount:
        _logger.info("Removed %s legacy ir.model.fields rows", cr.rowcount)

    for xml_id in _DROPPED_VIEW_XMLIDS:
        module, name = xml_id.split(".", 1)
        cr.execute(
            """
            SELECT res_id FROM ir_model_data
             WHERE module = %s AND name = %s AND model = 'ir.ui.view'
            """,
            (module, name),
        )
        row = cr.fetchone()
        if row:
            view_id = row[0]
            cr.execute("DELETE FROM ir_ui_view WHERE id = %s", (view_id,))
            cr.execute(
                "DELETE FROM ir_model_data WHERE module = %s AND name = %s",
                (module, name),
            )
            _logger.info("Removed legacy view %s (id=%s)", xml_id, view_id)
