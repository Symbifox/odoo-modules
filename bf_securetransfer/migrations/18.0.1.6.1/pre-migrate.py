"""Refresh the reformatted secure-message template on existing installs.

18.0.1.6.1 rewrites mail_template_secure_message (single-line paragraphs so the
en_CA term map matches, plus the str()-based hook fix) and completes the en_CA
translation of the module. The template is noupdate, so — as in 18.0.1.6.0 —
delete this one record here; the data load recreates it from the new XML and
post-migrate re-applies the en_CA translations."""


def migrate(cr, version):
    cr.execute(
        "SELECT res_id FROM ir_model_data "
        "WHERE module = 'bf_securetransfer' "
        "AND name = 'mail_template_secure_message'"
    )
    row = cr.fetchone()
    if not row:
        return
    cr.execute("DELETE FROM mail_template WHERE id = %s", (row[0],))
    cr.execute(
        "DELETE FROM ir_model_data "
        "WHERE module = 'bf_securetransfer' "
        "AND name = 'mail_template_secure_message'"
    )
