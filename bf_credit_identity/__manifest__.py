# -*- coding: utf-8 -*-
{
    "name": "Credit & Identity",
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "summary": "Check your credit files and protect your identity: a guide to "
               "your rights in Quebec and Canada, with private reminders",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "auto_install": False,
    # Léger exprès : `mail` pour les activités (les rappels), rien d'autre.
    # Aucune intégration aux agences d'évaluation du crédit : rien ne sort de
    # l'instance, aucune donnée de crédit n'est saisie.
    "depends": ["mail"],
    "description": """
Credit & Identity
=================

A guide and private reminders to keep an eye on one's credit files (Equifax,
TransUnion) and to react to identity theft. Written for people living in
Quebec, with notes for the rest of Canada.

* **Guide**: credit file versus credit score, how to get both for free, what to
  check, the Quebec Credit Assessment Agents Act (security freeze, security
  alert, explanatory note, costs, delays, duration), warning signs of identity
  theft and what to do, short good practices. Every fact cites an official
  source, with the date it was read.
* **Reminders**: a starter calendar (Equifax then TransUnion six months apart,
  monthly statements, yearly review of the protective measures, renewal of a
  six-year alert). Each reminder becomes an activity for its owner a few days
  before it is due, with no email sent.

Who sees what
-------------
Reminders are **private**: each person sees only their own, administrators
included. A reminder has no followers and its thread notifies nobody. A system
administrator can still change the security rules themselves: the privacy holds
between people, not against whoever administers the database. No link to the
credit bureaus, no credit data stored: only dates, a name and a free note.

Labels are written in English; French (Canada) comes from ``i18n/fr_CA.po``.
""",
    "uninstall_hook": "uninstall_hook",
    "data": [
        "security/ir.model.access.csv",
        "security/credit_identity_rules.xml",
        "data/mail_activity_type_data.xml",
        "data/ir_cron_data.xml",
        "views/credit_guide_templates.xml",
        "views/credit_reminder_views.xml",
        "wizard/credit_setup_wizard_views.xml",
        "views/credit_identity_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_credit_identity/static/src/js/credit_guide_action.js",
            "bf_credit_identity/static/src/xml/credit_guide_action.xml",
            "bf_credit_identity/static/src/scss/credit_guide.scss",
        ],
    },
}
