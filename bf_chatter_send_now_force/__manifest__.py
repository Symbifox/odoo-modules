{
    "name": "BF Chatter — Force Send on Scheduled Send Now",
    "version": "18.0.1.0.0",
    "summary": "Cliquer « Send Now » sur un courriel planifié envoie immédiatement (pas dans 5 min)",
    "description": """
Odoo upstream omits ``mail_notify_force_send=True`` in
``mail.scheduled.message.post_message`` (UI button), so the resulting
mail.mail waits up to 5 minutes for the ``Mail: Email Queue Manager`` cron.
The same model's daily cron ``_post_messages_cron`` already injects this
flag — this module restores parity for the UI button.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "category": "Productivity",
    "license": "LGPL-3",
    "depends": ["mail"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
