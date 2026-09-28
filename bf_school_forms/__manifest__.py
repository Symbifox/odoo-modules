{
    "name": "Symbifox École : autorisations",
    "version": "18.0.1.0.1",
    "category": "Education/School",
    "summary": "Field trip and activity authorisations: one click per guardian, dated "
               "evidence, a refusal always wins",
    "description": """
Authorisations
==============

- the school office asks the families of chosen groups to authorise an
  event (field trip, activity, overnight stay), with an answer deadline;
- one answer per student, one email per guardian who signs, with a personal
  link that works without a portal account;
- "I authorise" or "I refuse" in one click, recorded with the date, the
  channel, the IP address and the fingerprint (SHA-256) of the text answered.
  A sent text cannot be edited;
- one guardian is enough by default (Civil Code art. 603); a type may
  require every guardian who signs (overnight stay);
- a refusal always wins: one parent's "no" keeps the child home;
- reminders in one click, closing at the deadline by a daily cron, and a
  count of the students without any guardian who signs.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal"],
    "data": [
        "security/bf_school_forms_security.xml",
        "security/ir.model.access.csv",
        "data/bf_school_form_data.xml",
        "data/mail_template.xml",
        "views/school_form_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
