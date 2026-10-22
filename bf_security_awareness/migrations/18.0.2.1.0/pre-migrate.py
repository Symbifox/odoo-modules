"""18.0.2.1.0 : faire recréer le gabarit d'invitation à la formation.

Son corps change : plus de coquille ni de bleu codé en dur,
l'envoi passe la mise en page de la société, et le bouton, la date et la
signature reprennent ceux de la relance du registre (`bf_training`). Le
destinataire passe de `email_to` à `partner_to`, et l'expéditeur à la société.

Le gabarit est dans un bloc `noupdate` : un `-u` ne le réécrit pas, et basculer
`ir_model_data.noupdate` ne sert à rien. Même geste que `bf_training` 18.0.1.2.0 :
on retire la paire enregistrement + `ir.model.data`, et le chargement la recrée.

🔴 Une migration qui lève bloque la base. Si une clé étrangère qui interdit la
suppression pointe vers le gabarit, on le laisse en place et on le dit au
journal : l'invitation garde son ancien corps, la montée passe.

⚠️ Un gabarit retouché à la main est remplacé (README).
"""
import logging

_logger = logging.getLogger(__name__)


def _references_bloquantes(cr, gabarit_id):
    """Les tables dont une ligne pointe vers le gabarit sans le laisser partir."""
    cr.execute("""
        SELECT c.conrelid::regclass::text, a.attname
          FROM pg_constraint c
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
         WHERE c.confrelid = 'mail_template'::regclass
           AND c.contype = 'f'
           AND c.confdeltype IN ('a', 'r')
    """)
    bloquantes = []
    for table, colonne in cr.fetchall():
        cr.execute('SELECT 1 FROM %s WHERE "%s" = %%s LIMIT 1' % (table, colonne),
                   (gabarit_id,))
        if cr.fetchone():
            bloquantes.append("%s.%s" % (table, colonne))
    return bloquantes


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT id, res_id FROM ir_model_data
         WHERE module = 'bf_security_awareness'
           AND name = 'mail_template_training_invite'
           AND model = 'mail.template'
    """)
    row = cr.fetchone()
    if not row:
        return
    xmlid_id, gabarit_id = row
    bloquantes = _references_bloquantes(cr, gabarit_id)
    if bloquantes:
        _logger.warning(
            "bf_security_awareness : gabarit d'invitation %s laissé en place, "
            "référencé par %s. Il garde son ancien corps.",
            gabarit_id, ", ".join(bloquantes))
        return
    cr.execute("DELETE FROM mail_template WHERE id = %s", (gabarit_id,))
    cr.execute("DELETE FROM ir_model_data WHERE id = %s", (xmlid_id,))
    _logger.info("bf_security_awareness : gabarit d'invitation %s retiré, "
                 "le chargement le recrée.", gabarit_id)
