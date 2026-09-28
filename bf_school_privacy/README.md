# Symbifox École: consents (`bf_school_privacy`)

Law 25 consents for a school, asked each year of the right person, on top of the
consent module (`privacy_consent`), which keeps the evidence, the versioned texts and
the portal pages.

## Features

- Four separate school purposes, each with a plain-language text in French and
  English (CAI criteria: specific, granular, temporary, understandable):
  - photos and videos inside the school;
  - photos and videos published outside the school;
  - family contact list of the group;
  - educational platforms outside the school (some hosted outside Québec).
- **Ask this year's consents** on the school form: opens the consent module's request
  for every student enrolled this year and the four purposes. Students already asked
  (pending or granted) are skipped.
- Under 14, the request goes to the adults with **parental authority** (the guardian
  links of `bf_school_core`); from 14, to the student (Private Sector Act s. 4.1 and
  14; Access Act s. 53.1 and 64.1). The minor flag is refreshed from the birth date
  at the moment of the request.
- A student the consent module would get wrong is left out and named in a note on the
  school: no birth date, a minor without an adult holding parental authority (the
  module would ask the child), or a recipient whose address is another account's login
  or whose account is archived (the request would fail).
- "My children" on the family portal links to the consents page.

## Before the first request

The texts say "the school". Each school adapts them before sending: the name of its
person in charge of personal information, and, for the educational platforms, the
list and hosting of the platforms it uses (a transfer outside Québec needs its own
privacy impact assessment).

## What has not been confirmed

- A student of 14 or more is asked by email: most have no address on file and no
  portal account yet, so their consents stay pending until the school collects them.
- `privacy_consent` sends each request at once, then deletes its own message, which
  deletes the email record with it. Without a working mail server nothing is left to
  show a failed sending, and the chatter still says "sent". Not changed here: the
  module is shared by every tenant.
