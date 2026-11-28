{
    "name": "BF Project Beneficiary",
    "version": "18.0.1.0.1",
    "category": "Services/Project",
    "summary": "Who pays and who receives, told apart on the project: the "
               "ordering customer keeps the billing, the beneficiary is named "
               "beside it, and projects group by ordering customer",
    "description": """
Ordering customer and beneficiary
=================================

A project carries one customer in Odoo, and the whole billing chain descends
from it. Hand-kept project books name two parties: the one that orders and
pays, and the one the work is done for.

This module keeps the standard customer as **the ordering customer**, the
payer that the invoices and the analytic lines follow, and adds the
**beneficiary** beside it. Leaving the beneficiary empty says both are the same
party, which is the case for most projects.

It reads on the form under the ordering customer, as an optional list column,
in the search bar, and it groups: a menu entry opens the projects already
grouped by ordering customer.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["project"],
    "data": [
        "views/project_views.xml",
    ],
    "installable": True,
    "application": False,
}
