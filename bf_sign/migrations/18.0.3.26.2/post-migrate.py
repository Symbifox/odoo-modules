"""Write the reminder's expiry date in French, like the sentence around it.

The reminder template is ``noupdate="1"``, so the corrected XML never reaches
an installed database on its own. Rather than unlink and recreate it (which
would wipe a tenant's own edits to the reminder), replace only the one
expression, in every language slot of the body, and only where it still reads
exactly as shipped: an edited body is left alone.
"""
import logging

_logger = logging.getLogger(__name__)

_OLD = "dt_format='d MMMM y')"
_NEW = "dt_format='d MMMM y', lang_code='fr_CA')"


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE mail_template t
           SET body_html = replace(t.body_html::text, %s, %s)::jsonb
          FROM ir_model_data d
         WHERE d.module = 'bf_sign'
           AND d.name = 'mail_template_sign_reminder'
           AND d.model = 'mail.template'
           AND t.id = d.res_id
           AND t.body_html::text LIKE %s
    """, (_OLD, _NEW, "%" + _OLD + "%"))
    _logger.info("bf_sign 3.26.2: reminder expiry date now in French (%s template)",
                 cr.rowcount)
