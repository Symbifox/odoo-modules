# Symbifox École: report cards (`bf_school_report_card`)

Elementary and secondary report cards made from the gradebook, sealed when issued, read by the
families in the portal, and the written communications the regulation requires.

## Features

- **A report card per student and term**, generated from the gradebook: the figures are never
  typed, only the teachers' comments (each on their own subject) and the homeroom teacher's
  general comment and year-end decision. Regenerating a draft keeps the comments.
- **Results as whole percentages** by competency or component, the discipline result and the group
  average (I-13.3, r. 8, s. 30.1 and 30.2). The last report card of the year adds the final
  result (20/20/60), the ministerial exam, the final group average and, at the secondary
  level, the units earned.
- **Issuing seals it**: a JSON snapshot with its SHA-256, filed as the "school results" piece of
  the student's file with its seal, after one last reading of the gradebook; the PDF is printed from
  the snapshot, so a name corrected later cannot change what was sent. "Check the seal" tells whether a change was made behind the application.
  A correction is a new version that replaces the old; the old one stays.
- **Deadlines**: the term's deadline flags a report card past it, and records when one was issued
  late.
- **The PDF** is rendered by a cron from the sealed content, never in a request a family waits on. Its
  SHA-256 is written when it is rendered and compared each time it leaves: a file that no longer matches
  (replaced behind the application) is not given out, and the access log of the student's file says so;
  rendering it again from the sealed content is the office's button, and the thread keeps the fingerprint
  it replaces. Each download (the route of the module, `/web/content`) writes a line in that access log
  (`bf_school_record`); nothing in a cache.
- **Family portal**: the holders of parental authority read the issued, shown report cards of their
  children, with a read receipt. A student of age gets their own: the parents do not see it. An email
  names the child, never describes them. Between the issue of a correction and its publication the family
  keeps the report card it had; the email that shows a correction says it is a correction.
- **Corrections and the deadline**: a correction is judged on the deadline its first version met or missed;
  one made after the deadline of a report card that was on time is not "late".
- **Written communications**: the first one (at the latest October 15, s. 29) is prepared for each
  student; the monthly information of s. 29.2 (results that may not reach the threshold, conduct,
  intervention plan) is prepared from the gradebook for the first case and entered by hand for the
  others; both are sent by email or marked sent when given on paper.

## Security

The office generates, issues and shows. A teacher **listed on a group** reads the report cards of that
group, whole (every subject, the general comment, the year-end decision, the drafts, the versions and the
thread); at the secondary level a group lists all its teachers, so each of them reads the whole card of its
students. A teacher of one subject who is **not** listed on the group reads the lines of their subject
only, and comments them on the screen "Comments on the subjects": the card itself (the homeroom teacher's
comment, the decision, the thread, the other versions) is not theirs. A teacher never writes a figure. The
state, the seal and the publication move with their buttons, by RPC too. Families read through their own
guardian links. The rules of a teacher have no clause of year: a teacher stays able to read the report cards
of a group of a past year for as long as they are listed on it, so take them off the group when the year is
closed.

Several companies in one database are kept apart: the report cards, their lines and their components, the
communications and the read receipts have a rule of company, and every method that issues, shows, renders,
corrects, generates the report cards of a group or prepares the communications of a year reads its records
as the user before it runs as the superuser (a group, a term or a year of another company is refused). There
is no separation between the schools of one company: the office of a company sees all its schools.

The thread of a report card or of a communication is the staff's: only users of the school follow it or
receive its messages (a family never does; it is told by the email of the publication), a follower must be
able to write the document, and its messages are not edited or deleted by hand. An email is deleted once it
is sent: a communication keeps the adults it was sent to (and the thread says it, without the text), and
showing a report card leaves who was told in the thread. A communication is "sent" only once every email
is in the queue, and the subject of a message is one line.

The day a report card is judged late (issued or shown after the deadline) is the day in the time zone of the user who issues or shows it, UTC when their profile has none: set the time zone of the office's users.

## What has not been confirmed

- **The layout is ours.** It carries what s. 30.1 requires (identification, results by competency,
  discipline results and group averages, comments, and on the last report card the final results and
  the decision), but the annexes V to VII are images in the published regulation, not text: compare
  with the school's usual template before first use.
- The preschool report card (annex IV: statements on the development of competencies, not
  percentages) is not covered.
- The pass rules (s. 28) are the school's: the module records the decision, it does not take it.
- Sending a report card by email outside the portal is not offered: the family reads it in the portal.
