# -*- coding: utf-8 -*-
{
    "name": "Daily To-Do Digest",
    "version": "18.0.2.1.0",
    "category": "Productivity",
    "summary": "Daily email digest with overdue and today's activities, tasks, and subtasks",
    "description": """
Daily To-Do Digest
==================

Sends a daily email at 4 AM with:
- Overdue activities (mail.activity)
- Today's activities
- Overdue project tasks and subtasks
- Today's project tasks and subtasks
- Upcoming tasks preview (configurable 1-7 days)
- 7-day week preview with daily task/activity counts
- Clickable links to each item
- Test button to send only to current user
- Inspirational quote (rotating from 120 quotes)
- Weather forecast with emoji icons (temperature, precipitation, high/low)

Features:
- Email preheader for quick preview in email clients
- Timezone-aware date handling (America/Montreal)
- Symbifox branding

Uses Symbifox branding.
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    # ⚠️ `bf_meeting` n'est PAS une dépendance de manifeste, volontairement.
    # Ce module est sous LGPL-3 alors que `bf_meeting` est sous BUSL-1.1 : une
    # dépendance dure ferait promettre à la licence permissive quelque chose
    # qu'elle ne peut pas tenir, puisqu'on ne pourrait pas installer ce module
    # sans accepter des conditions restrictives. Le bloc « rencontres » du
    # sommaire détecte donc le modèle à l'exécution
    # (`_get_meetings_buckets` → `env.get("meeting.dashboard")`, qui rend un
    # dictionnaire vide quand le modèle est absent). Rien n'est perdu quand
    # `bf_meeting` est installé, et le module s'installe seul sinon.
    "depends": [
        "base",
        "mail",
        "project",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/inspirational_quotes.xml",
        "data/daily_digest_cron.xml",
        "views/res_users_views.xml",
        "views/daily_digest_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
