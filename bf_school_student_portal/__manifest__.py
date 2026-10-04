{
    "name": "Symbifox École : l'élève au portail",
    "version": "18.0.1.0.1",
    "category": "Education/School",
    "summary": "Students sign in to the family portal with their school directory account, "
               "the one that opens the school's computers",
    "description": """
Students on the portal
======================

- each student directory account (Authentik) gets a portal user tied to the
  student: no password of its own, the student signs in through the school's
  directory, with the same user name and password as on the school's computers;
- the portal user follows the account: suspended (archived) when the student is
  no longer enrolled, active again when they are;
- signing in through the school's directory never creates a user, whatever the
  website's sign-up setting: only a directory account does;
- a student does not change their own contact card nor delete their account:
  the school keeps them.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_device", "auth_oauth", "privacy_consent"],
    "data": [
        "views/school_views.xml",
        "views/portal_templates.xml",
        "data/ir_cron.xml",
    ],
    "installable": True,
    "application": False,
}
