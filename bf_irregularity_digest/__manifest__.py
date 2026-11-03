{
    "name": "BF Irregularities — Daily digest section",
    "summary": "Adds the reader's open irregularities to the daily digest",
"description": """
Irregularities in the daily digest
==================================

Bridge between ``bf_irregularity`` and ``daily_todo_digest``: the reader's open
irregularities appear in the daily digest, beside their activities, tasks and
meetings.

* One line per finding, coloured by severity, most serious first.
* Only the findings owned by the reader — the section never widens what the
  registry's record rule allows.
* Capped at fifteen rows, with a link to the whole registry beyond that.
* Nothing at all when the reader has no open finding.
    """,
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_irregularity", "daily_todo_digest"],
    "data": [
        "views/daily_digest_views.xml",
    ],
    "installable": True,
    "auto_install": True,
}
