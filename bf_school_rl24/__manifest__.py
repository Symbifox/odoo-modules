{
    "name": "Symbifox École : relevé 24",
    "version": "18.0.1.0.1",
    "category": "Education/School",
    "summary": "Child-care fees paid, by calendar year, payer and child, for the Relevé 24: "
               "annual statement on the family portal, export for the school's slip software",
    "description": """
Relevé 24
=========

For Québec schools that provide child care in the sense of the Taxation Act
(s. 1029.8.67): pre-K and kindergarten, a real child-care service.

- what each payer paid on the contract invoices' child-care lines, by
  calendar year and child, net of what was paid back after a termination;
- a credit note is not a payment, and meals billed apart never count (they
  are board, s. 1029.8.68);
- a company or a bursary fund gets no statement: the slip goes to the person
  who paid;
- the office reviews and issues the annual statements; an issued statement
  that no longer matches the payments is flagged;
- the family reads and prints its statements on the portal;
- an export for the software that produces the official slips.

This module does not produce the official Relevé 24 nor transmit it to
Revenu Québec, and it does not collect social insurance numbers.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_billing", "bf_school_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_rl24_security.xml",
        "report/rl24_report.xml",
        "wizard/rl24_compute_views.xml",
        "views/rl24_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
