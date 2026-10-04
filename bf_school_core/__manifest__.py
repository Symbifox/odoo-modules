{
    "name": "Symbifox École",
    "version": "18.0.1.0.2",
    "category": "Education/School",
    "summary": "Schools, school years, levels, groups, students and their guardians, "
               "with roles for shared custody",
    "description": """
School
======

The foundation of the Symbifox school suite, for Québec schools.

- schools, school years (one current year per school), levels from pre-K to
  Secondary V, groups with their teachers;
- students are contacts: birth date, permanent code (code permanent),
  enrolments in groups;
- guardians are linked to each student with explicit roles: parental
  authority, receives notices, can sign, pays, may pick up, emergency contact,
  lives with. Shared custody is a set of links, not a special case;
- the guardians with parental authority are mirrored to the consent module
  (privacy_consent), so consents and signatures reach them unchanged;
- a student is a minor for consent purposes until the age set by the
  company's privacy framework (14 under Québec's Law 25), computed from the
  birth date and switched automatically on the birthday.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["mail", "contacts", "privacy_consent"],
    "data": [
        "security/bf_school_security.xml",
        "security/ir.model.access.csv",
        "data/bf_school_level_data.xml",
        "data/ir_cron.xml",
        "views/school_views.xml",
        "views/group_views.xml",
        "views/res_partner_views.xml",
        "views/guardian_link_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": True,
}
