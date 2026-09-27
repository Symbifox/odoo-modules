{
    "name": "Symbifox École : santé",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "Health alerts for every staff member, detailed health records and medication "
               "for a restricted group, parents' authorisations signed on the portal",
    "description": """
Health
======

Health information is sensitive (Private Sector Act s. 12) and reserved to those who
need it (s. 20). A privacy impact assessment is required before the school puts it in
service (s. 3.3): a French template ships with the module (doc/).

- level 1, every staff member: a short health alert on the student (severe or to
  know), shown on the form and the student list;
- level 2, the Health group the school names (not the Administration by default):
  the detailed record, the individual emergency plan, the medication and the
  register of doses;
- medication authorisation: a prescribed medication (Professional Code s. 39.8),
  one of the routes it allows, dosage, validity period, conditions for an
  as-needed medication; signed by a guardian who signs, on the portal, with the
  fingerprint of the text signed; a signed authorisation is not edited;
- register of doses: nothing is given without a valid authorisation, except
  epinephrine in an emergency, which any staff member records and which tells
  the family at once;
- the family portal (Health): the alerts, the authorisations to sign, what is
  authorised and what was given.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal"],
    "data": [
        "security/bf_school_health_security.xml",
        "security/ir.model.access.csv",
        "data/health_data.xml",
        "views/health_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
