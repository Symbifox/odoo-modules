"""18.0.11.59.0 : les envois automatiques déjà partis, en partie.

À partir de cette version, chaque envoi automatique est capté à l'envoi
(`bf_email_avis`). Pour le passé, c'est un rattrapage
PARTIEL : seulement ce qui garde un destinataire, c'est-à-dire un message dont
les destinataires figurent encore sur le message (`partner_ids`) ou sur un
`mail.mail` qu'Odoo n'a pas supprimé. Une infolettre dont le courriel a été
supprimé après l'envoi n'a plus de destinataire : elle reste dehors.

Même création, même propriétaire et même dédoublonnage qu'à l'envoi.
"""
import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import email_normalize_all

_logger = logging.getLogger(__name__)

LOT = 500


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Email = env["bf.email"]
    from odoo.addons.bf_email_management.models.bf_email_avis import TYPES_AVIS
    cr.execute(
        """
        SELECT m.id
          FROM mail_message m
         WHERE m.message_type IN %s
           AND NOT EXISTS (SELECT 1 FROM bf_email b WHERE b.mail_message_id = m.id)
           AND coalesce(m.model, '') NOT IN ('mailing.contact', 'mailing.mailing')
           AND (EXISTS (SELECT 1 FROM mail_mail x
                         WHERE x.mail_message_id = m.id AND x.state = 'sent')
                OR EXISTS (SELECT 1 FROM mail_notification n
                            WHERE n.mail_message_id = m.id
                              AND n.notification_type = 'email'
                              AND n.notification_status = 'sent'))
         ORDER BY m.id
        """, [TYPES_AVIS])
    ids = [r[0] for r in cr.fetchall()]
    # Les infolettres envoyées à des fiches ordinaires (piste, contact) se
    # reconnaissent à leur trace d'envoi de masse, quand le module est là.
    cr.execute("SELECT to_regclass('mailing_trace') IS NOT NULL")
    if cr.fetchone()[0] and ids:
        cr.execute(
            """
            SELECT m.id FROM mail_message m
              JOIN mailing_trace t ON t.message_id = m.message_id
             WHERE m.id IN %s
            """, [tuple(ids)])
        masse = {r[0] for r in cr.fetchall()}
        ids = [i for i in ids if i not in masse]
    internes = Email._bf_avis_adresses_internes()
    crees = 0
    for debut in range(0, len(ids), LOT):
        messages = env["mail.message"].browse(ids[debut:debut + LOT])
        courriels = env["mail.mail"].search([("mail_message_id", "in", messages.ids),
                                             ("state", "=", "sent")])
        par_message = {}
        for m in messages:
            adresses = set()
            for p in m.partner_ids:
                adresses |= set(email_normalize_all(p.email or ""))
            par_message[m.id] = {"message": m, "a": adresses, "createur": m.create_uid}
        for x in courriels:
            entree = par_message[x.mail_message_id.id]
            entree["a"] |= set(email_normalize_all(x.email_to or ""))
            entree["a"] |= set(email_normalize_all(x.email_cc or ""))
            for p in x.recipient_ids:
                entree["a"] |= set(email_normalize_all(p.email or ""))
        # Un point de sauvegarde par message : une erreur SQL sur un message
        # (fiche disparue, contrainte) ne doit pas faire échouer la montée.
        for mid, entree in par_message.items():
            try:
                with cr.savepoint():
                    crees += len(Email._bf_creer_avis({mid: entree}, internes))
            except Exception:
                _logger.warning("bf_email 11.59.0 : avis du message %s non créé", mid,
                                exc_info=True)
    _logger.info("bf_email 11.59.0 : %s message(s) automatique(s) candidat(s), %s avis créé(s)",
                 len(ids), crees)
