"""18.0.1.1.0: the sending channel.

The ``channel`` column is born filled by the default, evaluated ONCE for the
whole table: "email" on a database without text messages. Yet a request
already sent could ONLY have gone out by text message, the only channel that
existed. It gets it back: otherwise its reminder would go out by email, to an
address nobody entered.

Drafts keep the database's default, and receive the address on their client's
contact when there is one, as the form would: without it, every draft already
prepared would refuse to be sent by email.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE bf_document_request
           SET channel = 'sms'
         WHERE state <> 'draft'
           AND channel IS DISTINCT FROM 'sms'
    """)
    cr.execute("""
        UPDATE bf_document_request AS r
           SET email = p.email
          FROM res_partner AS p
         WHERE p.id = r.partner_id
           AND r.state = 'draft'
           AND COALESCE(r.email, '') = ''
           AND COALESCE(p.email, '') <> ''
    """)
