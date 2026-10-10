# -*- coding: utf-8 -*-
{
    "name": "Household Base",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "The household group of a Symbifox Personal instance: who belongs to the household, "
               "at most ten accounts, and never an administrator among them",
    "description": """
The small common ground of a Symbifox Personal household instance.

* The "Household user" group: an internal account of a person of the household.
  The modules of the household extend it with the apps they open.
* At most ten active accounts per household (the Personal plan), whatever the
  path: the users screen, an invitation, a reactivation.
* A household user is never an administrator: a system administrator can read
  every private app of the household. Other modules add the groups that see a
  whole private class to the refused list.

Labels are written in English; French (Canada) comes from ``i18n/fr_CA.po``.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["base"],
    "data": [
        "security/household_base_security.xml",
    ],
    "pre_init_hook": "pre_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
