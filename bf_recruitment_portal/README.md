# Recruitment: candidate portal (`bf_recruitment_portal`)

Before this module, the person who applied sees **nothing**. Neither
`hr.applicant` nor `hr.candidate` is portalised, no controller exposes them, and
no account is created. The only channel is email, and the right of access is
exercised by writing to somebody who prepares the answer by hand.

This module gives the candidate their own view, through **two doors**: a signed
link they receive by email, and an account they can create for themselves. Both
lead to the same page.

## What is visible, and when

**Before the decision**: the state, and nothing else. The job, the submission
date, the number of interviews held. The list of sessions returns an EMPTY list
and the reason is not served. Showing appraisals mid-process would poison the
process and expose third-party opinion before anyone has decided.

**After the decision**: the decision, the written reason, the sessions, and the
interview book as a PDF. The guard reads **three signals** rather than one:
`decision_date`, the refusal reason, and the hire.

⚠️ The book served is `report_interview_book_candidate`, never the internal one:
it removes the names of the raters and of the person who decided. A pair of
tests compares the two outputs on the same file.

## The two switches the tenant holds

Both live on `res.company` (Recruitment > Configuration), not in an instance
parameter: a group that recruits in two companies does not necessarily want the
same regime on both sides, and the portal serves files that belong to a specific
company.

| Switch | Default | What it does |
|---|---|---|
| `recruitment_portal_book_enabled` | on | Unticking it removes **self-service**, not the right of access: the decision and the written reason are still served, and the page says where to ask for the rest |
| `recruitment_portal_otp_required` | off | A six-digit code sent to the address **on the file** before the page opens |

🔴 **The route enforces both, not only the template.** Hiding a button is not an
access control: `/my/candidature/<id>/cahier` re-reads the switch and redirects,
so a saved URL does not walk past a setting.

## The one-time code, and what it honestly protects

⚠️ **The link arrives by email too.** Against a compromised mailbox, a code sent
to that same mailbox adds nothing, and claiming otherwise would be dishonest. It
protects against a link that **leaked without the mailbox**: forwarded, left in
a browser history, in a proxy log, or read over a shoulder. That is a real risk
for a file carrying written appraisals, and it is why the setting exists. It
ships unticked: a refused candidate who cannot read their own file is a real
cost, to be weighed against it.

How it is built:

- 🔴 **A keyed digest, never a bare `sha256`.** The code is six digits and the
  prefix would be a constant, not a salt: the whole 10^6 space precomputes in
  under a second, and the digest would protect nothing from someone reading a
  backup. `odoo.tools.hmac` keys the digest on the database secret.
- **The challenge lives in the visitor's session**, with a fifteen-minute expiry.
  The code itself never leaves the model.
- **Constant-time comparison** (`hmac.compare_digest`): a comparison that stops
  at the first differing character can be timed.
- **Two counters, and the second one matters.** One caps failed codes per (IP,
  application); the other caps successful **sends**. Without the second,
  whoever holds the link floods the person's mailbox.
- **The address is never chosen by the visitor**: it is the one the file
  carries. Otherwise the code would be a send-to-any-address service.
- **The page shows a hint, not the address** (`l...e@example.ca`): publishing the
  full address would hand it to whoever holds a leaked link, which is precisely
  the person the code exists against.

## Security, by allow-list

⚠️ **Templates NEVER receive the record**, only dictionaries built by the
model's allow-lists. That is what makes ratings, rater names and internal notes
unreachable from a page, even if a template is edited later by inattention.

⚠️ **No ORM right is granted to the portal group.** Searches run under `sudo`
with a domain bounded to the partner, and single-record access goes through
`_document_check_access`, which accepts either a signed token or a user who
genuinely has read rights.

## The link that no longer leads anywhere

🔴 **An access failure used to redirect to `/my`**, which for a visitor with no
account is the LOGIN page. The link in a refusal letter therefore read like a
broken account, when the file had simply been destroyed at the end of the
retention schedule. The person concluded there was an outage, or that an account
had been taken away from them.

A page now says what is true, with a 404: the file is no longer available,
application files are not kept indefinitely, and here is where to ask.

🔴 **The SAME page for a destroyed file and for a wrong token.** Telling the two
apart would tell whoever is trying numbers which ones carried an application. A
test compares the two responses, bodies included, after neutralising the
requested id (which the visitor sent themselves, and which therefore reveals
nothing further).

⚠️ The page names nobody, confirms no application, and **does not assert that
the file was destroyed**: it says what is true in both cases.

## Traps paid for while writing this

- 🔴 **Odoo 18: `signup_token` is no longer a field of `res.partner`.** The token
  is produced inside `_get_signup_url_for_action()`. Checking the field raises an
  `AttributeError`; it is the URL that must be checked.
- ⚠️ **Sign-up goes through an INVITATION**, not through a bare `/web/signup`:
  the instance refuses uninvited registration (`allow_uninvited = False`, `b2b`
  scope), and that is the right setting. The link carries a token that works
  despite it.
- ⚠️ **`signup_prepare()` writes `signup_type` persistently**, so a naive
  "Create my account" button led to the login page on the first click and then
  hid itself on later ones.
- ⚠️ **Loose coupling**: `bf_recruitment_mail` does not depend on the portal. The
  link only appears if `access_url` exists on the model. Without that guard,
  rendering would raise on every tenant without the portal.

## 🔴 What the portal changes about the privacy regime

The right of access stops being a request handled by hand and becomes
**automatic and permanent**: every interview appraisal is in effect handed to
its subject once the decision is taken. The book's comment field already tells
raters "write it knowing the person evaluated has the right to read it". This
module turns that sentence into a fact. Say it to panels before enabling the
module.
