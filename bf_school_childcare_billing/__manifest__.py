{
    "name": "Symbifox École : facturation du service de garde",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "Child care billed each month to the adults who pay: rates, monthly tiers, late pick-up, "
               "books of tickets, and the Relevé 24",
    "description": """
Child care billing
==================

- rates per school and year: occasional period or day, day without class, child not booked,
  short notice, late pick-up by blocks of minutes, book of tickets; each one child care for
  the Relevé 24 or not, with a cap per unit;
- regular registrations billed on the contract (nothing more here) or each month by a tier of
  periods per week, prorated on the open days of a month started or ended mid-way;
- a ledger of what is due, computed again until it is billed, never changed once billed;
- on the first of the month, one invoice per adult who pays, shared as the contract says, sent
  from the school; the books of tickets pay the occasional periods first;
- books of tickets bought on the portal and paid online;
- the Relevé 24 counts what was paid for child care, in the year of the services.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_childcare", "bf_school_billing", "bf_school_rl24", "account_payment"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_childcare_billing_security.xml",
        "data/ir_cron.xml",
        "views/billing_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
