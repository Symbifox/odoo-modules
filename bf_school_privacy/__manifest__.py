{
    "name": "Symbifox École : consentements",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "Law 25 consents for a school: photos, publication, family contact list, "
               "educational platforms, asked each year of the right person",
    "description": """
School consents
===============

Built on the consent module (privacy_consent), which keeps the evidence, the
versioned texts and the portal pages.

- four separate school purposes, each with a plain-language text in French
  and English: photos inside the school, photos published outside, family
  contact list of the group, educational platforms outside the school;
- "Ask this year's consents" on the school: one consent per student and per
  purpose, for the students enrolled this year, skipping those already asked;
- under 14, the request goes to the adults with parental authority (the
  guardian links of the school foundation); from 14, to the student;
- the family portal links "My children" to the consents page.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal", "privacy_consent"],
    "data": [
        "security/bf_school_privacy_security.xml",
        "data/privacy_school_data.xml",
        "views/school_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
