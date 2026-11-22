# Survey Answer Sheet

A completed response files itself: a branded PDF of that one respondent's
answers, encrypted with the cohort's password, deposited in the respondent's
own folder.

## Why

A completed questionnaire is a document somebody will read, keep and share.
Odoo offers two ways to get one out of the database, and neither of them is
that document:

- the **statistics page** is about the survey, not about one respondent: it
  answers "how many said Large", and it carries counters, bars and a column
  called *User answers* that means nothing on a single response;
- the **printable page** is the questionnaire, which the reader prints by hand,
  one response at a time.

Neither carries the day the response was completed, and both leave the filing
to a person. What ends up in a client folder is then whatever somebody printed
that day: a browser print of the statistics page, dated nowhere, or a sheet
produced by a script outside Odoo.

## What it does

From a single trigger — the submission — and never from the respondent's own
request:

- **One branded PDF per response.** The questions in the survey's own order,
  section by section, with the answers as the respondent gave them: free text
  with its line breaks, choices as a list, the comment box as a comment,
  matrices and percentage allocations row by row, and *No answer* where there
  is none. The company's colours, font and logo, the completion date in the
  header, *Confidential* beside it.
- **Numbered like the form (18.0.1.1.0).** When the survey numbers its
  sections and questions with `bf_survey_numbering`, the sheet carries the same
  numbers — "2. Your company", "2.1 Founding year" — so "question 2.1" points
  at the same line on the form and on the sheet. The number is read only when
  that module is installed; this one does not depend on it.
- **Encrypted, AES-256.** The user password is the survey's own, common to the
  cohort of people who will read the sheets, so that a file that strays is
  useless to whoever finds it. The owner password is drawn at random and kept
  nowhere: nobody can lift the document's restrictions, including us. Left
  empty, the survey's password is drawn at random the first time a sheet is
  filed, twenty characters from an alphabet with no look-alikes.
- **Filed in the respondent's own folder.** The company name the respondent
  declared is matched against the folders under the root you name, ignoring
  legal forms (*inc*, *ltée*, *ltd*…) and the words you list as noise — the
  programme name the folders carry, typically. One folder matches and the sheet
  goes in; none matches and the folder is made, copied from the template you
  name; **several match and nothing is filed**, because a sheet in the wrong
  company's folder is a leak, not a mistake.
- **Several destinations.** The subfolders you list, one per line, each created
  if missing: the mandate's folder and the one shared with the client, for
  instance.
- **It never overwrites somebody else's file.** Every write is conditional: it
  creates the file, or replaces the exact version this response wrote earlier.
  A refused write is then looked at, and three things can be there. This
  response's own earlier copy — recognised by a marker carried *inside the
  encryption*, which only the password reveals — is replaced. **Another
  response's sheet** is left alone and this one is filed beside it, under a
  numbered name: two people of one company answering the same day is ordinary,
  and neither sheet may erase the other. Anything else is a file this module
  did not write: nothing is touched, and the response says so. A file we knew
  and that changed on Nextcloud since is in that last group — somebody's work,
  not a version to replace.
- **The response keeps its copy**, attached in Odoo, so the sheet is found
  again without opening Nextcloud.
- **The password exists before the first response does.** It is drawn when the
  filing is switched on, not when the first sheet is filed: a filing that fails
  halfway is rolled back, and a password drawn inside it would be rolled back
  too — leaving a file on Nextcloud whose marker the next attempt could no
  longer read. Changing the password afterwards is allowed and changes nothing
  for the sheets already filed; it only means a retry on one of those paths
  will leave the old file alone rather than replace it.
- **A courier, not a gate.** The filing runs in a scheduled action every ten
  minutes. A Nextcloud outage delays a sheet; it never breaks a submission.
  Each attempt is counted, each failure written in full on the response, and
  after five the response waits for a person instead of knocking forever.

Nothing is sent to anybody: this module files. The password is handed over out
of band, by whatever channel you trust — a one-time secret, for instance.

## Configuration

On the survey, *Options* tab, **Answer sheet**:

| Field | What it is |
|---|---|
| File an answer sheet | The switch. Test entries are never filed. |
| Nextcloud | The configuration whose service account writes. |
| Client folders root | The folder holding one folder per company. |
| Destination subfolders | One per line, relative to the company's folder. Empty files in the company's folder itself. |
| Folder template | Folder, under the same root, copied to make a company's folder. Empty makes an empty one. |
| Words ignored when matching | Space-separated. The programme words your folders carry. |
| File name | `%(date)s` (2026_11_19), `%(year)s`, `%(month)s`, `%(day)s`, `%(company)s`, `%(survey)s`, `%(token)s`. |
| Answer sheet password | The cohort's password. Readable by the settings group, not masked: a password nobody can read is a password nobody can deliver. |

Which company a response belongs to is read, in order, from the invited
partner's own company — on an invited survey, a name somebody at the house
typed — then the response's *nickname*, which is where Odoo stores the answer
of the question marked **Save as nickname**, so the company question can be
worded however the survey wants, then the partner's name, then the email's
domain.

## What it does not promise

- **The company a sheet belongs to is a string the respondent typed.** On a
  public survey that string is the only signal there is: somebody who types the
  exact name of an existing folder files their sheet there. Equality of words
  stops the near-misses, not a name typed on purpose. Where that matters, send
  the survey by invitation — a response that carries a contact is routed by the
  contact's own company, which somebody at the house typed.
- **The Print button renders the sheet in the clear**, for internal users who
  can already read the answers in Odoo. The encryption protects the *filed*
  copy, the one that travels to a shared folder.
- **The password sits in the database in clear**, fenced to the settings group.
  Anyone who can read the database can read it; it is a key shared with a
  cohort of readers, not a credential.
- **A question the respondent never saw is not printed at all.** A conditional
  question whose trigger was not answered is left out rather than printed as
  *No answer*, which would make the sheet say something false.

## What it does not do

- It does not deliver the password. That is the point of encrypting.
- It does not notify anybody. Pair it with an automated action if you want an
  email when a response arrives.
- It does not share the folder it writes in: the Nextcloud permissions are
  whatever they were.
- It does not file a response more than once per destination path: a retry
  replaces its own file, it never leaves a second copy beside it.
- It does not undo a folder it made. Nextcloud has no transaction: a company
  folder created just before a deposit fails stays there, empty, and the retry
  uses it.

## Requires

`survey`, `bf_document_nextcloud_sync`, and the `pyhanko` Python package for
the encryption — already present in every image that signs PDFs.
