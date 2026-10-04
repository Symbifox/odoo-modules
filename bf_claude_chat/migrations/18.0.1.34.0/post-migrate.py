"""18.0.1.34.0 : ce qui existait avant le non-lu est réputé lu.

Sans cela, la première liste après la montée mettrait en gras chaque
conversation où Gen a déjà répondu, et le gras ne dirait plus rien.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE claude_chat_session s
           SET seen_message_id = m.dernier
          FROM (SELECT session_id, MAX(id) AS dernier
                  FROM claude_chat_message
                 GROUP BY session_id) m
         WHERE m.session_id = s.id
           AND COALESCE(s.seen_message_id, 0) < m.dernier
    """)
