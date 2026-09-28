{
    "name": "Symbifox École : portail des familles",
    "version": "18.0.1.0.1",
    "category": "Education/School",
    "summary": "One portal account per adult for all their children: groups, teachers, "
               "and what the school allows them to do",
    "description": """
Family portal
=============

- one account per adult, whatever the number of children or schools;
- "My children": each child with their current groups, levels and teachers,
  and the roles the school recorded for this adult (receives notices, signs,
  pays, may pick up);
- the school invites the guardians who receive notices, from a student or
  from the whole school, in one click. Adults without a valid email, or
  already invited, are counted and skipped, never guessed.

Families never read the school's tables directly: every portal page reads
through the adult's own guardian links, so a parent sees their children and
nobody else's, even by calling the server by hand.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_core", "portal"],
    "data": [
        "views/portal_templates.xml",
        "views/backend_views.xml",
    ],
    "installable": True,
    "application": False,
}
