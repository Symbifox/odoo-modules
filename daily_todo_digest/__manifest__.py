# -*- coding: utf-8 -*-
{
    "name": "Daily To-Do Digest",
    # 18.0.2.2.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.2.3.0: les événements du calendrier de la journée, lus par la
    #   présence (pas par l'organisateur : la synchro Nextcloud pose
    #   __system__), dans le fuseau du destinataire et dans son fuseau
    #   secondaire. Les en-têtes et pastilles de couleur d'accent prennent une
    #   variante assombrie : un bleu de marque clair sous du texte blanc rendait 2,6:1.
    # 18.0.3.0.0: less noise (recurring reminders folded into one line per
    #   model, tasks waiting on someone else counted apart), per-recipient
    #   preferences (hour, rhythm, sections in three states), templates that
    #   target user groups with exactly one digest per person, and the
    #   "Watched subjects" section read from the RSS module at runtime.
    #   Three defects fixed: deadlines were shown in the server's time zone
    #   instead of the recipient's, the "upcoming tasks" section was gathered
    #   and never rendered, and texts were not escaped. The list links now
    #   carry their domain in a server-side action: Odoo 18 reads no domain
    #   from a URL, so they opened the home screen or an unfiltered list.
    "version": "18.0.3.0.0",
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
- Today's calendar events (those the recipient attends)
- Upcoming tasks preview (configurable 1-7 days)
- 7-day week preview with daily task/activity counts
- Clickable links to each item
- Watched subjects: what the RSS lists that watch a name retained (bf_flux,
  detected at runtime, no manifest dependency)
- Test button to send only to current user
- Inspirational quote (rotating from 120 quotes)
- Weather forecast with emoji icons (temperature, precipitation, high/low)

Features:
- Email preheader for quick preview in email clients
- Timezone-aware date handling (America/Montreal)
- Symbifox branding

Noise control:
- Recurring reminders created by a scheduler (backup checks, log reviews)
  fold into one line per model, with the count and a link
- Tasks waiting on a client or a colleague leave the day's list and are
  counted in one line

Per-recipient preferences (own hour, own frequency including chosen days, own
sections in three states) and templates that target user GROUPS, each person
receiving exactly one digest.

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
