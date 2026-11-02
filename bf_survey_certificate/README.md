# Branded Course Certificates (`bf_survey_certificate`)

Certification documents in the company's own brand on Odoo 18 Community: a
branded PDF, a verifiable certificate number with its public page, and a
branded success email.

## Why

Odoo's certification document is Odoo-branded: hardcoded colours, Odoo's
fonts, the attempt's start date, and a sequential number nobody can verify.
This module adds three "Company brand" layouts to the certification template
choices, built from the company that owns the course.

## Features

- **Three branded layouts**: *panel* (dark side panel carrying the logo, QR
  and number), *minimal* (left-aligned, airy, accent rule on the edge) and
  *classic* (double frame, ribbons, faded logo watermark). All three take
  the company's brand colours, logo, font and signature. The signature
  image, the signatory's name and their title live on the company form
  (*Settings › Companies › Certificates*); without an image, the name and
  title print alone.
- **The course's name**: the document and its email name the eLearning
  course when the certification belongs to one, not the survey's own title
  (which often reads "Certification: X").
- **The right brand**: the issuing company is the one of the website the
  course is published on, so a multi-company database issues each
  certificate under the brand the participant enrolled with.
- **The right day**: the date shown is the day the participant succeeded,
  in the participant's time zone, not the day they started.
- **Verifiable number**: every successful certification receives a random,
  non-guessable certificate number. Anyone holding the number can check the
  certificate at `/certificat/<number>` (or `/certificate/<number>`), which
  shows the holder's name (never an email address), the course, the
  completion date and the issuing company - and answers the same way for a
  wrong number and a malformed one. Certifications passed before the module
  was installed get their number at install.
- **Branded email**: a success email wrapped in the house email layout
  carries the PDF and the verification link, with a body matching the
  survey's chosen layout. It is installed turned off: pick it as the
  survey's *Certified Email Template* to turn it on.
- **Withdrawal**: the participation form shows the certificate number and a
  *Withdraw the certificate number* button (survey managers only). Once
  withdrawn, the public page stops answering for that number.
- **Participant's language**: the PDF renders in the participant's
  language; the public page answers in the language of its route.

## Access

The public page is anonymous and shows nothing but what the certificate
itself prints; it never prints an email address. The signature image on the
company is readable by internal users only.

## Configuration

1. *Settings › Companies*, *Certificates* tab: the signature image, the
   signatory's name and their title.
2. On the certification survey, pick one of the *Company brand*
   certification templates (panel, minimal or classic), and optionally the
   *Branded Certification Success* email as the survey's *Certified Email
   Template*.

## Known limits

- The brand colours come from the company's `report_brand_*` fields (the
  Blue Fox onboarding base is a dependency), with the document layout
  colours as fallback for a company that has not set them.
- Certificates downloaded before an upgrade keep the PDF saved at the time
  (the report action stores `certification.pdf`): purge the saved
  attachment to re-render them with the current layout.
- The withdrawal guard is the global *Survey manager* group: the core
  survey module is not multi-company, so a survey manager of one company
  can withdraw any company's certificate number.
- Re-issuing a number after a withdrawal has no screen: calling
  `_bf_ensure_certificate_code()` over RPC is the assumed gesture.
- The survey form's *Preview* button creates an entry that FAILS whenever
  the success threshold is above 0, so the preview shows the failure page.
  To compare the three layouts, temporarily set the threshold to 0 or
  render a real passed participation.

## License

Business Source License 1.1, see `LICENSE`. Production use for your own
internal operations is allowed; providing it as a service to third parties
requires an agreement with Les services de consultation Blue Fox, Inc.
