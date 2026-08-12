# -*- coding: utf-8 -*-
{
    "name": "Daily To-Do Digest",
    "version": "18.0.2.0.0",
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
- Blue Fox branding

Uses Blue Fox branding.
    """,
    'author': 'Blue Fox Inc.',
    'website': 'https://bluefoxconsultant.com',
    'license': 'LGPL-3',
    "depends": [
        "base",
        "mail",
        "project",
        "bf_meeting",
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
