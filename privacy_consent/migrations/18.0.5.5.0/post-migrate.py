"""Les courriels d'un consentement de mineur vont à ses responsables.

Seule la demande passait par les responsables légaux. Les rappels, l'avis
d'expiration et les deux confirmations avaient `partner_to` = la personne
concernée : pour un mineur, ils partaient à l'enfant avec le lien, qui lui
permettait d'accorder (rappels) ou de retirer (confirmations) à la place du
responsable. Le code envoie désormais tout par `_send_consent_mail`, et le champ
« À » des gabarits rend les mêmes destinataires pour un envoi à la main.

Les gabarits sont en `noupdate` : la mise à jour ne réécrit pas leur `partner_to`.
On le repointe ici, seulement s'il vaut encore la valeur livrée. Une valeur
modifiée à la main est journalisée et laissée telle quelle. Rejouée, la
migration ne trouve plus rien à faire.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

GABARITS = (
    "mail_template_consent_request",
    "mail_template_consent_expiring",
    "mail_template_consent_reminder_1",
    "mail_template_consent_reminder_2",
    "mail_template_consent_renewal_confirmation",
    "mail_template_consent_granted_confirmation",
)
ANCIEN = "{{ object.subject_partner_id.id }}"
NOUVEAU = "{{ object._privacy_mail_partner_to() }}"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in GABARITS:
        template = env.ref(f"privacy_consent.{xmlid}", raise_if_not_found=False)
        if not template:
            continue
        cr.execute("SELECT partner_to FROM mail_template WHERE id = %s", [template.id])
        valeur = (cr.fetchone()[0] or "").strip()
        if valeur == NOUVEAU:
            continue
        if valeur != ANCIEN:
            _logger.warning("privacy_consent 18.0.5.5.0 : %s, « À » modifié à la main (%r), laissé tel quel",
                            xmlid, valeur)
            continue
        cr.execute("UPDATE mail_template SET partner_to = %s WHERE id = %s", [NOUVEAU, template.id])
        template.invalidate_recordset(["partner_to"])
        _logger.info("privacy_consent 18.0.5.5.0 : %s, « À » repointé vers les destinataires", xmlid)
