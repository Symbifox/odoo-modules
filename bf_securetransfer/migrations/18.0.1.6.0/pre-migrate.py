"""Let the reworked secure-message template reach already-installed instances.

The mail templates live in a ``noupdate="1"`` data block so operator edits
survive upgrades — but that same flag means the XML body is NEVER re-applied on
``-u`` (odoo/tools/convert.py short-circuits the update on the block's noupdate
attribute, before the per-record ir_model_data flag is even consulted).

18.0.1.6.0 reworks ``mail_template_secure_message`` (it is now also used for
message-only transfers, and its wording adapts to the OTP/password gates), so
this one record must be refreshed. A noupdate block still CREATES missing
records, so we delete this template here (pre-migrate, before the data load):
the load then recreates it from the new XML exactly as a fresh install would,
and post-migrate re-applies the en_CA translation. Scoped to this single
xmlid — the other three templates keep their operator edits.

Deleting the template is safe: every FK into mail_template is ON DELETE SET NULL
(server actions, compose wizards) or CASCADE on link tables this body template
never populates (attachments, reports, activity types). It is sent from Python
via send_mail(), not from a stored server action, so no live record points at it.
"""


def migrate(cr, version):
    cr.execute(
        "SELECT res_id FROM ir_model_data "
        "WHERE module = 'bf_securetransfer' "
        "AND name = 'mail_template_secure_message'"
    )
    row = cr.fetchone()
    if not row:
        return  # never installed (fresh install loads the new XML directly)
    cr.execute("DELETE FROM mail_template WHERE id = %s", (row[0],))
    cr.execute(
        "DELETE FROM ir_model_data "
        "WHERE module = 'bf_securetransfer' "
        "AND name = 'mail_template_secure_message'"
    )
