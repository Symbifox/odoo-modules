"""Reprise des ordres du jour déjà envoyés.

Avant cette version, `sent_date` portait deux sens à la fois : « un envoi a été
amorcé » et « l'ordre du jour est parti ». Le champ `email_sent_date` isole
désormais le second. Impossible de savoir après coup, pour les enregistrements
existants, si le courriel a réellement quitté Odoo — et les traiter comme non
envoyés ferait pleuvoir des activités de rappel sur des rencontres passées. Ils
sont donc repris comme envoyés.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE meeting_agenda
           SET email_sent_date = sent_date
         WHERE sent_date IS NOT NULL
           AND email_sent_date IS NULL
    """)
    cr.execute("""
        UPDATE meeting_agenda
           SET send_state = CASE
                 WHEN email_sent_date IS NOT NULL AND sent_manually THEN 'manual'
                 WHEN email_sent_date IS NOT NULL THEN 'sent'
                 WHEN sent_date IS NOT NULL THEN 'prepared'
                 ELSE 'not_sent'
               END
    """)
