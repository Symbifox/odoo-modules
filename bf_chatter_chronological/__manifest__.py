{
    "name": "BF Chatter Chronological View",
    "version": "18.0.4.0.0",
    "summary": "Order the chatter feed by the email's original Date header",
    "description": """
Sort the Odoo chatter by the original email Date header (mail.message.date)
instead of the insertion id. Three layers:

1. Python `_order` on mail.message -> `date desc, id desc`
2. Python `_message_fetch` override -> pagination via compound (date, id) cursor
3. JS patch on `Thread.fetchMessages` / `fetchMoreMessages` / `fetchNewMessages`
   -> final date-based re-sort to defeat the front-end's id-only sort

Composite Postgres index `mail_message_model_res_id_date_id_idx` is created
in `_auto_init` so chatter fetches on large records stay fast.

Bonus: cogwheel action « Réordonner ce chatter par date » that scans the
current record's messages and re-parses the original Date header from quoted
body content when the import lost it.
    """,
    "author": "Blue Fox Inc.",
    'website': 'https://bluefoxconsultant.com',
    "category": "Productivity",
    'license': 'LGPL-3',
    "depends": [
        "mail",
        "project",
        "crm",
        "account",
        "hr_expense",
        "helpdesk_mgmt",
        "bf_meeting",
    ],
    "data": [
        "data/actions.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_chatter_chronological/static/src/thread_model_patch.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
