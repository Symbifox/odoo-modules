"""18.0.4.10.0 : expéditeur nommé des gabarits clients, vues orphelines archivées.

Les gabarits sont en noupdate : un -u ne réécrit pas leur email_from. On le
remplace ici, seulement là où il porte encore la valeur d'origine ; une retouche
à la main reste en place.
"""
import logging

from odoo.addons.bf_helpdesk.models.orphan_views import archive_orphan_helpdesk_views

_logger = logging.getLogger(__name__)

ANCIEN = "{{ (object.team_id.alias_id.display_name or object.company_id.email or '') }}"
REMPLACEMENTS = {
    "mail_template_ticket_ack": (ANCIEN, "{{ object._bf_email_from() }}"),
    "mail_template_waiting_reminder": (ANCIEN, "{{ object._bf_email_from() }}"),
    "mail_template_waiting_autoclose": (ANCIEN, "{{ object._bf_email_from() }}"),
    "mail_template_csat_v2": (
        "{{ (object.ticket_id.team_id.alias_id.display_name or object.ticket_id.company_id.email or '') }}",
        "{{ object.ticket_id._bf_email_from() }}"),
    "mail_template_client_update": (
        "{{ (object.team_id.alias_id.display_name or object.company_id.email or user.email_formatted or '') }}",
        "{{ object._bf_email_from() or user.email_formatted }}"),
}


def migrate(cr, version):
    if not version:
        return
    faits = []
    for name, (avant, apres) in REMPLACEMENTS.items():
        cr.execute("""
            UPDATE mail_template t SET email_from = %s
              FROM ir_model_data d
             WHERE d.res_id = t.id AND d.model = 'mail.template'
               AND d.module = 'bf_helpdesk' AND d.name = %s AND t.email_from = %s
        """, (apres, name, avant))
        if cr.rowcount:
            faits.append(name)
    # Vues orphelines qui masquaient les vraies (voir models/orphan_views.py).
    archive_orphan_helpdesk_views(cr)
    _logger.info("bf_helpdesk 18.0.4.10.0 : expéditeur nommé sur %s gabarit(s) : %s",
                 len(faits), ", ".join(faits) or "aucun")
