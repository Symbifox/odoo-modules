# Symbifox École: Charlemagne (`bf_school_charlemagne`)

The student headcount, the local results and the ministerial exams sent to the Ministry of
Education's Charlemagne system: XML, signed and encrypted, put on the Ministry's SFTP server.

Source: the Ministry's teletransmission guide **P560C, version 1.1 of 2026-09-21** (sections 3 to 7
and 9). The XML structures of the transactions come from its tables, generated, not retyped.

## Features

- **What the Ministry asks and the school's records do not hold** (codes of languages, of places
  of birth, teaching order, the names of the Ministry's register) is entered once per student and
  follows them from year to year. The names must be confirmed against the Ministry's register
  (Ariane): the declaration is refused when they do not match.
- **Transactions**: the declaration of the headcount (DJ), the local results (RJ), the
  registrations to the ministerial exams (IJ) and their results (UJ), made from the school's
  records, plus the requests for a file (lists, feedback of the declarations, exam registrations…).
  A ledger of what the Ministry accepted says which is a creation, which a modification and which
  nothing; it is written when an accepted answer comes back, never when a file is sent. It is kept
  **per mode**: what a rehearsal accepted is not what the Ministry accepted.
- **Local results (RJ)** are made once the third term is closed, and never for a course that ends with
  a ministerial exam (the Ministry computes that result from the school's and the exam's), nor for a
  course whose component weights do not total 100 % (the report card refuses the same course; the
  transmission says so under "Left out"). A result already accepted is modified under the key the Ministry
  holds (student, course, year, session, group number): the guide does not let a modification change
  them (6.24), so if the school's group number or month has moved since, the new result is sent for the
  record as it is and the transmission says so.
- **Exam results (UJ)** carry the school's final result with its rank in fifths and the raw scores,
  except for the August session: the guide refuses a school result then (CHS-340838, unless the Ministry
  accepted an exception for the school, the exam and the group, which the module does not know) and a rank
  without a school result (CHS-340010), so an August result carries the exam's scores only. A course
  whose component weights do not total 100 % sends its exam scores without the school's result.
- **What goes away is taken away.** An element the school no longer has (a part's score put back to
  "not entered", an exam that became "absent") is taken off with an empty tag (guide 6.29). A student's
  rank alone is never taken off, nor a school result that is only unconfirmed. When an exam sitting
  is moved or removed, the results of the old sitting are taken off first, and the registration is
  cancelled (IJ SUP) in the next transmission, because the Ministry refuses to cancel a registration for
  which results were received (CHA-600814); the cancellation of a sitting that is left goes before the
  registration of the new one. The Ministry can still refuse a cancellation: its answer says so.
- **Checks before anything leaves**: each transaction is checked against the guide's structures
  (required elements, lengths, kinds, dates, Latin-1). One that fails is shown with its reason and
  the file is not sent partially.
- **The file**: ISO-8859-1, with the guide's header, signed with the school's key and encrypted with
  the Ministry's (OpenPGP, binary), uploaded as a `.part` and renamed once complete.
- **The answers**: the control reports are fetched, decrypted, checked against the Ministry's
  signature and read; each transaction is accepted, accepted with a message or rejected, in the
  Ministry's own words. Each request has its own report: the transmission is answered when every one
  is in. A report that lists only what has a message accepts the rest by its counters, and says so when
  the counters do not add up. A report that lists no request and none of the transmission's transactions
  is kept, said (LOCAL-021) and not counted as an answer; a file that could not be read stays on the
  server, and the "Read again the files not read" button reads it again. A command the Ministry refuses
  as a whole (a message of the command that rejects it, or every request refused) ends the transmission
  as "Refused by the Ministry": nothing was processed, the ledger is as it was and a new transmission
  makes the same transactions again. Files that are not reports are kept as they came. A file is taken for a
  transmission by its exact name (the name the school gave it ends with a letter, so `…_1e` is never
  `…_15e`) and removed from the server only on a later pass, once it was read and saved.
- **Simulation**: the whole circuit with a stand-in for the Ministry that accepts what is
  well-formed. Nothing is encrypted or sent, and **no copy of the file is kept**, neither the plain
  XML nor an encrypted one: only the file's own fingerprint. The transactions and the ledger of a
  rehearsal do keep the elements, **the students' identities among them** (names, permanent code,
  postal code, codes of languages): the rehearsal goes on from them (a creation, then a modification).
  Deleting a rehearsal takes away its transactions, messages, answers and the reports it kept; its
  ledger lines stay. A transmission sent in production is the school's record of what the Ministry
  received and is kept.
- **Collecting while an answer is awaited**: the ledger is written by the answers, so a collection made
  while an earlier transmission is still "sent" collects again what that one holds, and says so; the Ministry
  refuses a creation it already has.
- **An attendance declaration (FRE)** needs the date attendance started (DT_DEBUT_FREQN: optional for a
  financing declaration, required for an attendance one, guide 6.1): without it the student is shown
  with the reason.

## Security

The server, the account and the keys are the system administrator's: the office reads whether they
are set, never what they are. Secrets are encrypted with the server's key (`bf_policy` escrow) and
used in a throw-away keyring that is wiped after the call; a new host or account drops the old
password, and neither a host nor an account can be taken for an option of `ssh` (a host holds no "@": `ssh`
cuts `account@host` at the last one). The settings, which
choose where the secrets go, are the administrator's to create and change (the office only
prepares and sends). The server's host key must be entered (`known_hosts` line): credentials are never sent to
an unchecked host. Nobody edits a transaction, the ledger or a message.

## What has not been confirmed

- **Nothing here has been accepted by the Ministry.** The guide has no homologation: the school holds
  the SFTP access and the keys, and a first transmission should be a small one in the days after
  the school's access is granted. What the Ministry checks with its own tables (the declaration
  alone has more than two thousand lines of rules, section 7.2.3.3) is not reproduced.
- **The domain tables** (codes of languages, municipalities, programs, teaching order, the code of
  English as the language of instruction) are not in the guide. They are entered by the school; the
  module checks their format, not their value.
- Transactions not covered: adult education (FGA), vocational training (FP), preschool-only
  declarations (DS), addresses (AD, AA), participation in courses (PC), equivalences (EA), the
  declarations of services (SA, SP), the per-question results of an exam.
- **The rank (RANG_CINQM)**: the guide says only that it is the student's rank among the students of the
  group for the course, one character. The module works it out in fifths from the school's final results
  of the students still in the group (1 is the first fifth); that this is what the Ministry means has not
  been confirmed. For the August session the guide asks a rank for every result of the general education
  of young people (CHS-340011) and forbids one without a school result (CHS-340010), which August forbids
  too: the module follows the two that agree and sends none; what the Ministry answers has not been seen.
- **A cancellation of a registration** is sent when the results of the sitting are off; whether the
  Ministry then takes it (CHA-600814 speaks of results "received") has not been seen. A refusal comes back
  as a message and the cancellation is made again at the next collection.
- The SFTP password path (`SSH_ASKPASS`) was not tried against a real server: the key path was, with
  a real `sshd`. The file name the Ministry gives its answers was read from the guide
  (`CHACO…`/`CHADE…`); a name that differs will not be matched to a transmission and stays on the
  server.
- A zero in an exam score is read as "not entered".
