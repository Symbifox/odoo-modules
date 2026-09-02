"""Les brouillons parqués AVANT le drapeau doivent le porter aussi.

Avant `bf_is_draft` (18.0.11.14.0), « garder un courriel sans l'envoyer » se
faisait à la main : on le PROGRAMMAIT à une date volontairement lointaine —
la sentinelle d'au moins quatre ans — et on l'envoyait soi-même le moment
venu. Ces lignes-là sont des brouillons ; rien dans la base ne le disait.

Sans cette reprise, elles resteraient rangées parmi les envois différés, la
liste annoncerait pour elles un envoi prévu en 2031, et le cron finirait par
les poster le jour où la sentinelle arrive — précisément ce que le drapeau
existe pour empêcher.

⚠️ Le seuil est celui de la convention, pas un chiffre rond : quatre ans.
En deçà, on ne peut pas distinguer un brouillon parqué d'un envoi différé
lointain mais voulu, et se tromper dans ce sens-là empêcherait un envoi que
quelqu'un attend. On ne touche donc que ce qui est clairement une sentinelle.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE mail_scheduled_message
           SET bf_is_draft = TRUE
         WHERE COALESCE(bf_is_draft, FALSE) = FALSE
           AND scheduled_date > (now() AT TIME ZONE 'UTC') + INTERVAL '4 years'
    """)
    if cr.rowcount:
        from odoo import api, SUPERUSER_ID
        env = api.Environment(cr, SUPERUSER_ID, {})
        env['ir.logging'].create({
            'name': 'bf_email_management',
            'type': 'server',
            'level': 'INFO',
            'dbname': cr.dbname,
            'message': (
                '%s envoi(s) programmé(s) au-delà de quatre ans reclassés en '
                'brouillon : ils étaient parqués à la sentinelle.' % cr.rowcount
            ),
            'path': 'migrations/18.0.11.14.1/post-migrate.py',
            'func': 'migrate',
            'line': '1',
        })
