# -*- coding: utf-8 -*-
{
    "name": "Gen — Cockpit",
    # 18.0.1.1.0: le résumé et la description étaient les dernières chaînes
    #   françaises d'un module à source anglaise ; ils passent à l'anglais, en
    #   reprenant les libellés que les vues portent déjà. Le français du
    #   résumé vit au catalogue ; une description ne se traduit pas.
    # 18.0.1.1.0: le module était déjà écrit en anglais, mais n'avait aucun
    #   catalogue français : un usager réglé en fr_CA le lisait en anglais.
    #   i18n/fr_CA.po et le .pot du module portent maintenant le français.
    "version": "18.0.1.1.0",
    "category": "Productivity",
    "summary": "Admin view of Gen sessions: stream failures and token usage",
    "description": """
The administration cockpit for Gen, split out of ``bf_claude_chat`` so the base
module stays what everyone gets: the chat itself.

Two screens, both restricted to ``base.group_system``:

* **Cockpit** — sessions and their stream failure counter. A session that
  reaches three consecutive failures stops resuming its thread and forks a
  fresh one on the next message. The button clears the counter once the cause
  is understood.
* **Usage** — tokens and API-equivalent cost per person and per week.
  Informational, deliberately kept apart from operations.

Installing this module adds both entries under Gen's administration menu. Not
installing it takes nothing away from the chat.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_claude_chat"],
    "data": [
        "views/cockpit_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
