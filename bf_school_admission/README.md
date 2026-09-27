# Symbifox École: admission and re-enrolment (`bf_school_admission`)

Admission of new students and re-enrolment of current ones, for Québec private schools.

## Features

- **Campaigns** per school and coming school year: levels offered, opening and
  closing dates, fee, exam date and place, text of the public form.
- **Public form** (`/school/admission/<campaign>`): the student, one or two guardians,
  up to five documents (PDF, JPEG, PNG, HEIC, 10 MB each), and the acknowledgement of
  how the information is used. A hidden field turns robots away. Guardians are always
  created as new contacts: a public form never attaches to an existing contact found
  by email, since anyone can type anyone's address. The office merges duplicates.
- **Fee as an invoice.** The fee is issued as a customer invoice, without tax
  (educational services are exempt). The family pays it online from the invoice page
  (any payment provider the school set up, Stripe for instance) or at the office, which
  records the payment in Invoicing. Either way, the application moves on the moment the
  invoice is paid (`account.move._invoice_paid_hook`).
- **Caps** (Regulation E-9.1, r. 3): at most 50 $ to study an application (s. 11), at
  most 200 $ of registration fee for a re-enrolment (s. 12).
- **Steps**: convocation to the exam (email with date and place), score, rank per level
  by score, then a decision: accepted, waiting list or refused. The rank is an aid: a
  named person decides and the decision records who and when (Law 25, Private Sector
  Act s. 12.1: no decision based exclusively on automated processing).
- **Personal status page** for the family, and an email at each step (received,
  convened, accepted, waiting list, refused), one per guardian.
- **Enrol**: an accepted applicant becomes a student in one step, with the guardian
  links (the fee payer marked as paying).
- **Re-enrolment**: on the family portal, each child enrolled this year shows
  "Re-enrol for 2027-2028" to the adults who sign. One click issues the registration
  fee invoice; the re-enrolment is confirmed once it is paid. One re-enrolment per
  child and campaign.

## Security

The school office only. Teachers and families have no access right on applications;
families follow theirs through the personal link, checked in constant time. Another
family's re-enrolment page is a 404.

## What has not been confirmed

- No Moneris provider for Odoo 18: Stripe or the office's manual recording.
- Purging is a gesture of the office on a closed campaign ("Purge the applications not
  enrolled"), not automatic: the documents, the student's identity and the evaluation
  are destroyed, the fee invoice is kept (accounting). The form tells the families so.
- The registration fee is capped at 200 $; the other cap of s. 12 (1/10 of the contract
  price) is checked on the contract, not here.
- No exam timetable with several sessions: one date and place per campaign.
