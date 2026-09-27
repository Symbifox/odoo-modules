{
    "name": "Symbifox École : annonces aux familles",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "School announcements to families, by school or group, with an email that "
               "names the children and a dated read receipt on the portal",
    "description": """
Announcements to families
=========================

Bridge between the staff noticeboard (bf_babillard) and the family portal.

- a new audience, "School families": a whole school, or chosen groups of
  the current year;
- the families reached are the adults who receive notices for an enrolled
  student, computed on the day, never a list kept by hand;
- every family is told by email, in its own language, and the subject names
  its children ("Léa, Tom: Museum trip"), because a parent of three cannot
  guess which child a notice is about;
- the portal lists the school's announcements; an announcement that asks
  for confirmation carries an "I have read it" button, and the school sees
  who confirmed and when (bf_babillard's read receipts, reused as is);
- opening the email or the link never counts as a confirmation.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal", "bf_babillard"],
    "data": [
        "security/bf_school_message_security.xml",
        "security/ir.model.access.csv",
        "data/mail_template.xml",
        "views/babillard_post_views.xml",
        "views/portal_templates.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
