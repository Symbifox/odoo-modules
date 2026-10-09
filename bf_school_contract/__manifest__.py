{
    "name": "Symbifox École : contrat de services éducatifs",
    "version": "18.0.1.0.3",
    "category": "Education/School",
    "summary": "Québec private school contract for educational services: the Act's caps "
               "enforced, mandatory mentions printed, signed with Symbifox Sign",
    "description": """
Contract for educational services
=================================

For Québec private schools (Act respecting private education, E-9.1, s. 66-76,
a law of public order).

- detailed price: admission or registration fee, educational services,
  accessory services one by one, total;
- the caps are constraints, not warnings: eligibility fee at most 50 $,
  admission fee at most 200 $ or 1/10 of the total price, at least two
  instalments, cancellation indemnity or penalty at most 500 $ or 1/10 of the
  price minus the admission fee;
- instalment schedule, nothing due before the services begin but the
  admission fee;
- the contract document prints every mention of Regulation E-9.1, r. 1, s. 20,
  the full text of sections 70 to 75, the face-uncovered clause of s. 68.1 and
  the non-assignment sentence, followed by the signature space;
- sent for signature to every guardian who signs, through bf_sign; the
  contract turns to "Signed" when the request is completed;
- termination by the client: the amount the school may keep and the refund
  due within ten days are computed from the Act.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_core", "bf_sign"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_contract_security.xml",
        "data/ir_sequence.xml",
        "report/school_contract_report.xml",
        "views/school_contract_views.xml",
    ],
    "installable": True,
    "application": False,
}
