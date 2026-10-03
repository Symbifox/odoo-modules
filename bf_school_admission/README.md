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
  At most three applications an hour from one address, or for one email.
- **Fee as an invoice.** The fee is issued as a customer invoice, without tax
  (educational services are exempt). From the public form, the invoice stays a draft
  until the office has checked the application and clicks "Ask for the fee": an
  anonymous visitor posts no invoice. The families then receive the payment link. They
  pay online from the invoice page (any payment provider the school set up, Stripe for
  instance) or at the office, which records the payment in Invoicing. Either way, the
  application moves on the moment the invoice is paid (`account.move._invoice_paid_hook`).
  The invoice line names the application, never the child.
- **Caps** (Regulation E-9.1, r. 3): at most 50 $ to study an application (s. 11), at
  most 200 $ of registration fee for a re-enrolment (s. 12).
- **Steps**: convocation to the exam (email with date and place), score, rank per level
  by score, then a decision: accepted, waiting list or refused. The rank is an aid: a
  named person decides and the decision records who and when (Law 25, Private Sector
  Act s. 12.1: no decision based exclusively on automated processing).
- **Personal status page** for the family, and an email at each step (fee to pay,
  received, convened, accepted, waiting list, refused), one per guardian.
- **Enrol**: an accepted applicant becomes a student in one step, with the guardian
  links (the fee payer marked as paying).
- **Re-enrolment**: on the family portal, each child enrolled this year shows
  "Re-enrol for 2027-2028" to the adults who sign. One click issues the registration
  fee invoice; the re-enrolment is confirmed once it is paid. One re-enrolment per
  child and campaign, even with two clicks at once. Withdrawing an application cancels
  its unpaid fee.
- **The state moves with the buttons only**: the state, who decided and when, the
  invoice and the evidence cannot be written by hand, through the screen or by RPC.

## Security

The school office only. The Administration role does **not** imply Invoicing (it opens
every journal entry, payment and the employees' bank accounts): it lists the
applications and asks for the fee without it, and the school gives Invoicing to the
people who record payments at the counter. A fee invoice is confirmed only as a customer
invoice of the campaign's fee, in its currency, whether from the application or straight
from the invoice; and it cannot be deleted while its application is under way (once the
application is withdrawn, its cancelled draft can go). Teachers and families have no access right on
applications;
families follow theirs through the personal link, checked in constant time. Another
family's re-enrolment page is a 404.

The public form accepts at most 5 posts per address in 10 minutes, counted before the
documents are checked (valid or not), and at most 3 applications per address or per email
in an hour. An IPv6 address counts for its whole /64 (a home or a phone picks a new one in
it at will), and a /48 for at most 100 posts in 10 minutes; a port added by a proxy is
dropped. The first counts live in each server process's memory. The browser checks the
number and the size of the documents before sending: past the route's size limit, Odoo
answers before the form and the family would lose what it typed.

## What has not been confirmed

- No Moneris provider for Odoo 18: Stripe or the office's manual recording.
- Purging is a gesture of the office on a closed campaign ("Purge the applications not
  enrolled"), not automatic: the documents, the student's identity, the evaluation and
  its history are destroyed, the status link stops working, and the contacts the form
  created are deleted, or emptied when an invoice still needs them. The fee invoice is
  kept (accounting). An application accepted but not enrolled yet is not purged. The
  form tells the families so.
- The registration fee is capped at 200 $; the other cap of s. 12 (1/10 of the contract
  price) is checked on the contract, not here.
- No exam timetable with several sessions: one date and place per campaign.
