{
    "name": "Recevables en retard — section du digest quotidien",
    "version": "18.0.1.0.4",
    "category": "Accounting/Accounting",
    "summary": "Ajoute une section « Factures en retard » au digest quotidien "
               "et retire le courriel séparé de bf_invoice_followup",
    "description": """
Pont Relances de factures ↔ Digest quotidien
============================================

S'auto-installe quand ``bf_invoice_followup`` et ``daily_todo_digest`` sont
tous deux présents.

* Injecte une section « Factures en retard » dans le digest quotidien, à côté
  des activités, des tâches et des rencontres.
* **Désactive le cron du digest séparé** de ``bf_invoice_followup``
  (``cron_weekly_followup_digest``) : un courriel de moins, l'information au
  même endroit que le reste. La désinstallation du pont le réactive.
* La section ne s'affiche que pour les destinataires qui ont coché « Recevables
  en retard » dans leurs préférences — le pont ne change pas qui voit
  l'information financière.

Le cron des **activités** de relance (``cron_overdue_followup_activities``)
n'est pas touché : les activités qu'il crée remontent déjà dans les sections
« Activités » du digest.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": [
        "bf_invoice_followup",
        "daily_todo_digest",
    ],
    "data": [
        "views/daily_digest_views.xml",
        "views/res_users_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
    "auto_install": True,
}
