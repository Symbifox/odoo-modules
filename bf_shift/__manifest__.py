{
    "name": "Symbifox Quarts de travail",
    "version": "18.0.1.0.0",
    "category": "Human Resources/Employees",
    "summary": "Shift scheduling for regular and unionised employees: labour standards checks, "
               "premiums, call lists, swaps, taxable benefits, payroll export",
    "description": """
Shifts
======

Work schedules for regular and unionised employees in Québec.

- working conditions per group: the labour standards (LNT) are the floor, a
  collective agreement raises it, and the rule most favourable to the
  employee always applies;
- shift templates, schedules built from them, published then frozen, with a
  change log that nobody can edit (the evidence in a grievance);
- checks before publishing: 5-day notice, 2 hours beyond the usual day,
  14 hours in 24, 50 hours in the week, 32 hours of weekly rest, meal break
  after 5 hours, rest between shifts. They warn, they never block;
- open shifts offered down a call list by seniority, rotation or inverse
  seniority, with every offer, skip and refusal timestamped;
- swaps and give-aways between colleagues, approved by a manager;
- availability declared by the employees;
- pay: regular hours, overtime, double time, seventh day, time bank,
  call-back minimum, holiday indemnity (1/20), evening, night, weekend and
  holiday premiums with floors, enhanced rates and the majority rule;
- taxable benefits (overtime meals, taxi, parking) sorted for Québec and
  the CRA, with receipts;
- CSV export of coded hours for any payroll service. Deductions stay there.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["hr", "mail", "resource"],
    "data": [
        "security/bf_shift_security.xml",
        "security/ir.model.access.csv",
        "data/bf_shift_weekday.xml",
        "data/bf_shift_data.xml",
        "views/agreement_views.xml",
        "views/schedule_views.xml",
        "views/offer_views.xml",
        "views/pay_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": True,
}
