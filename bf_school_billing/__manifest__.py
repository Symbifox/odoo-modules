{
    "name": "Symbifox École : facturation des familles",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "Fee catalogue by level and profile, contracts priced from it with the "
               "sibling discount, one invoice a year per payer on the contract's "
               "instalments, bursaries, termination credited",
    "description": """
Family billing
==============

For Québec private schools, on top of the contract for educational services
(bf_school_contract).

- a fee catalogue per school year: educational services, accessory services and
  optional services, by level and profile, each marked as child care for the
  Relevé 24 or not;
- the contracts of a year prepared from the catalogue, with the sibling
  discount by rank in the family;
- the payers of a contract: shared custody split in percentages, a bursary or a
  third party paying a fixed amount;
- once the contract is signed and the office has checked its payers, one
  invoice per payer, its due dates taken from the contract's instalments
  (nothing due before the services begin but the admission fee), emailed to
  the payer, paid on the portal or recorded by the office;
- the amount already paid on the contract follows the invoices;
- a termination credits each payer for what the school may not keep; the
  refund due within ten days becomes an activity, with its deadline, for the
  person who recorded the termination.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_contract", "bf_school_admission", "bf_school_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_billing_security.xml",
        "data/ir_cron.xml",
        "views/fee_views.xml",
        "views/school_contract_views.xml",
        "views/menus.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
