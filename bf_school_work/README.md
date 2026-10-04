# Symbifox École: handing in work (`bf_school_work`)

Students hand in their work on the portal; the teacher returns a corrected copy and a mark.

## Features

- On a homework, the teacher chooses how it is handed in: **not online** (paper, class),
  **files**, or an **online quiz** (a scored survey). They attach **documents for the
  students**, set the total it is **marked out of**, and accept late work or not.
- One **hand-in** per student of the group, created with the homework (and for a student
  who joins later, on first need).
- **Portal** (**Work to hand in**): the student, with their own account, or an adult who
  holds **parental authority** hands in up to 5 files of 25 MB (PDF, images, office
  documents, audio, video, zip, Scratch), and replaces them until the work is returned.
  After the due date (end of the day at the school's time), work is flagged **late**, or
  refused if the homework does not accept late work.
- The teacher **returns** the work (one by one or from the list) with a **mark**, a
  **comment** and a **corrected copy**; the family sees them only once the work is
  returned. **Reopen** hides them again and lets the work be handed in again.
- **Online quiz**: the student (or the parent) opens their own answer from the portal;
  when the survey is finished, it counts as handed in and its score, out of the homework's
  total, becomes the mark. Reopening a quiz starts a new attempt; the first answer stays in
  the survey's results.
- The homework list of the portal links to the hand-in; a student's own account also sees
  their own homework and calendar.
- **Emails, offered by the school, chosen by each person.** The school ticks the emails it
  offers (school form, Work handed in): an email when work is returned (with the mark and a
  link to the work), and a daily summary (work returned, work to hand in within two days,
  work not handed in after its due date, new course contents when `bf_school_slides` is
  installed). Each adult with parental authority (and whom the school has not set to not
  receive notices), and each student with their own account, ticks on the portal the ones
  they want: nothing is sent until they do. One email per person, in their language, in the
  school's layout; the summary is sent only when there is something to say, and a returned
  work or a new content appears in it once. Its days are the school's days.

## Security

- The hand-ins have no portal access right: every portal page starts from the logged-in
  person (the student themselves, or the children they hold parental authority over) and
  answers 404 for anything else. An adult who only receives the school's notices sees the
  homework list, not the hand-ins nor the marks.
- An adult who only receives the school's notices never receives the work emails: they
  carry marks. Nor does a parent the school set to not receive notices.
- The work emails go only to a person with an active portal account, where they chose and
  can untick; to a student, only when the address on their card is theirs (a student's card
  often carries a parent's address, and an adult who may not receive these emails could read
  it).
- The daily summary is tied to no record: it never sits in the chatter of the parent's
  contact, which other employees may open. The return email is logged on the hand-in, read
  by the group's teachers and the office only.
- The choices are written by the portal page, for the person themselves and among what the
  school offers; anywhere else, by administrators only.
- Files are served as downloads, never inline, and only to the people above: the teacher's
  documents, the work handed in, and the corrected copy once returned.
- A teacher sees and returns the work of the groups they teach; the office sees all. What
  the family did and when (files, dates, state, author, lateness) is written by the system
  only: these fields are refused by RPC outside the module's own code.
- A file linked as a document or a corrected copy must have been uploaded on that homework
  or that hand-in: a teacher cannot hand out another record's file. The portal also checks
  it before serving.
- Once a student has handed in, the homework's group, hand-in mode and quiz no longer
  change; a teacher moves a homework only to a group they teach.
- Deleting a homework (or its group) deletes the hand-ins, their files and their log through
  the ORM. Nobody follows a hand-in or a quiz answer: nothing posted on it is emailed.
- A homework's quiz is locked when linked: by invitation, signed in, one attempt, and its
  answers read only by the group's teachers, the homework's teacher and the office (Odoo's
  Surveys user role otherwise reads every unrestricted survey). A "retry" shares the first
  answer's invitation: the first finished answer is the hand-in.
- Teachers who write quizzes need the Surveys user role; the school gives it.

## What has not been confirmed

- Annotating the copy in the browser is not there: the teacher annotates in a tool of their
  choice and uploads the corrected copy.
- The due time is the end of the due date; there is no due hour.
- The marks are not yet carried to a gradebook (report card lot).
