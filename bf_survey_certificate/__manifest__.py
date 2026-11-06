{
    "name": "Branded Course Certificates",
    # 18.0.1.1.1: the module icon, in the suite's family (the former one was a flat
    #   certificate on an opaque black square).
    "version": "18.0.1.1.1",
    "category": "Marketing/Surveys",
    "summary": "Certification documents in the company's own brand: a branded PDF, "
               "a verifiable certificate number with its public page, and a branded "
               "success email",
    "description": """
Branded course certificates
===========================

Odoo's certification document is Odoo-branded: hardcoded colours, Odoo's
fonts, the attempt's start date, and a sequential number nobody can verify.
This module adds three "Company brand" layouts to the certification template
choices - panel, minimal and classic - built from the company that owns the
course:

- the PDF takes the company's brand colours, logo, font and signature
  (signature image, signatory name and title live on the company form);
- the document and its email name the eLearning course when the
  certification belongs to one, not the survey's own title;
- the participation form shows the certificate number and offers its
  withdrawal: once withdrawn, the public page stops answering;
- the company is the one of the website the course is published on, so a
  multi-company database issues each certificate under the right brand;
- the date shown is the day the participant succeeded, not the day they
  started;
- every successful certification receives a random, non-guessable
  certificate number; anyone holding the number can check the certificate
  at /certificat/<number> (or /certificate/<number>), which shows the
  holder's name, the course, the completion date and the issuing company;
- a branded success email, wrapped in the house email layout, carries the
  PDF and the verification link, with a body matching the chosen layout.
  It is installed turned off: pick it as the survey's "Certified Email
  Template" to turn it on.

The brand colours come from the company's "report_brand_*" fields when the
Blue Fox onboarding base is installed, with the document layout colours as
fallback, so the module renders sensibly on a bare database too.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["survey", "bf_onboarding_base"],
    "data": [
        "report/certificate_templates.xml",
        "views/res_company_views.xml",
        "views/survey_user_input_views.xml",
        "views/verify_templates.xml",
        "data/mail_template_data.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
