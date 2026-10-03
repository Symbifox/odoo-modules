{
    "name": "Symbifox École : admission et réinscription",
    "version": "18.0.1.0.6",
    "category": "Education/School",
    "summary": "Admission of new students (public form, fee, exam, waiting list, decision by "
               "a person) and re-enrolment from the family portal",
    "description": """
Admission and re-enrolment
==========================

- admission campaigns per school and coming school year, with the levels
  offered, opening and closing dates, exam date and place;
- a public form: the student, one or two guardians, the documents (birth
  certificate, report card), the acknowledgement of how the information is used;
- the fee is issued as a customer invoice: the family pays it online from the
  invoice page (any payment provider the school set up, Stripe for instance) or
  at the office, which records the payment in Invoicing. Either way, the
  application is submitted the moment the invoice is paid;
- fee caps from Regulation E-9.1, r. 3: at most 50 $ to study an application
  (s. 11), at most 200 $ of registration fee (s. 12);
- convocation to the exam, score, rank per level as an aid, and a decision
  (accepted, waiting list, refused) always taken and signed by a person
  (Law 25: no decision based exclusively on automated processing);
- the family follows the application through a personal link and receives an
  email at each step;
- enrolment of an accepted applicant: the student and the guardian links are
  created in one step;
- re-enrolment: on the family portal, each child enrolled this year shows
  "Re-enrol for 2027-2028"; the registration fee invoice follows, and the
  re-enrolment is confirmed once it is paid.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal", "account_payment"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_admission_security.xml",
        "data/mail_template.xml",
        "views/admission_views.xml",
        "views/portal_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": ["bf_school_admission/static/src/js/admission_form.js"],
    },
    "installable": True,
    "application": False,
}
