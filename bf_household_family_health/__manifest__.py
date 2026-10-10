# -*- coding: utf-8 -*-
{
    "name": "Household Family: Healthy Fox",
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "summary": "A child of the household is one record, in the family and in Healthy Fox",
    "description": """
Bridge between Household Family (``bf_household_family``) and Healthy Fox
(``bf_health``).

* A person followed in Healthy Fox is a child of the household: one record, not
  two. Their parents are the child's parents: the primary parent holds the
  records, the second parent keeps them too.
* « Swap parents », or the departure of the parent who holds the records, hands
  them to the other parent.
* The birth date Healthy Fox follows (it decides the move at 14) is changed by
  Blue Fox only, as in Healthy Fox.
* An emergency card can show the person's active medications and conditions from
  Healthy Fox, only when the box is ticked. Healthy Fox has no allergy record:
  allergies are typed on the card.

Labels are written in English; French (Canada) comes from ``i18n/fr_CA.po``.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["bf_household_family", "bf_health"],
    "data": [
        "views/health_dependent_views.xml",
    ],
    "icon": "/bf_household_family_health/static/description/icon.png",
    "installable": True,
    "application": False,
    "auto_install": True,
}
