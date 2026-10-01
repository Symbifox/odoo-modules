"""La dernière activité de chaque conversation, prise à ses messages.

`write_date` ne suit pas l'activité (un message n'écrit pas la conversation) :
sans cette reprise, la passe de nuit prendrait pour neuve une conversation
muette depuis un mois, ou l'inverse. Une conversation sans message garde sa
date de création.

⚠️ Sans condition sur NULL : les deux colonnes naissent REMPLIES à l'heure de
la montée par leur défaut (`fields.Datetime.now`). Le script ne passe qu'une
fois, en montant d'une version antérieure.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE claude_chat_session s
           SET last_activity = COALESCE(
                   (SELECT max(m.create_date) FROM claude_chat_message m
                     WHERE m.session_id = s.id),
                   s.create_date)
    """)
    cr.execute("UPDATE claude_chat_session SET list_date = last_activity")
