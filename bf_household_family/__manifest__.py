# -*- coding: utf-8 -*-
{
    "name": "Household Family",
    "version": "18.0.1.1.0",
    "category": "Productivity",
    "summary": "The family of a household instance: members and their roles, children, "
               "family papers and emergency cards",
    "description": """
The family side of a Symbifox Personal household.

* Members: a household manager invites members, resends their password link and
  removes a teen or an account never used, without administration rights. An
  adult leaves the household by themselves, or Blue Fox removes them on request,
  with a delay. Nobody cuts another adult off from their own records.
* Children: one record per child, held by one or two named parents. The whole
  household sees the first name and the birthday; health and papers stay with
  the parents.
* Family papers: passports, health cards, licences and their expiry, kept in each
  person's private space and shared on purpose. No field for a social insurance
  number. Renewal reminders arrive as private to-dos.
* Emergency cards: seen by the household, printable, and shareable with a
  babysitter through a link that expires.

Labels are written in English; French (Canada) comes from ``i18n/fr_CA.po``.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": [
        "bf_household_base",
        "auth_signup",
        "contacts",
        "calendar",
        "project_todo",
    ],
    "data": [
        "security/family_security.xml",
        "security/ir.model.access.csv",
        "data/cron.xml",
        "report/emergency_card_report.xml",
        "views/emergency_public_templates.xml",
        "wizards/member_invite_views.xml",
        "wizards/member_leave_views.xml",
        "wizards/member_removal_views.xml",
        "wizards/emergency_link_wizard_views.xml",
        "views/member_views.xml",
        "views/departure_views.xml",
        "views/child_views.xml",
        "views/document_views.xml",
        "views/emergency_views.xml",
        "views/menu.xml",
    ],
    "icon": "/bf_household_family/static/description/icon.png",
    "installable": True,
    "application": True,
    "auto_install": False,
}
