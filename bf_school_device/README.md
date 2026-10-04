# Symbifox École: students' computers (`bf_school_device`)

The school's old computers, refurbished with Blue Fox OS, in the students' hands: in
the lab and lent for home. Part of Symbifox École.

## Features

- **Student accounts** in the school's directory (Authentik): one per student enrolled
  this year ("Create this year's accounts" on the school). The user name is drawn from a
  sequence (`e00001`), never the permanent code (it spells the name and birth date).
  Groups come from the student's level and class (`students`, `students-sec1`,
  `students-301`).
- **The accounts follow the enrolments.** Active while the student is enrolled in the
  current year, suspended otherwise. Every 15 minutes a job carries the changes to the
  directory by comparing what it must hold with what it was last sent, so a change of
  class, level or name reaches it without anyone thinking of it. A failure is written
  on the account and does not stop the others.
- **Passwords a young student can type**: two words and four digits
  (`renard-lune-4821`), drawn on request, set in the directory and shown once, for five
  minutes, to the person who drew it. Not stored.
- **Lent computers**: a computer enrolled under a loan profile (bf_policy shared seats)
  is lent to a student who has an account. Recording the loan writes the student as the
  machine's borrower; the machine opens to them at its next policy sync. Returning it,
  or marking it lost, closes it again. One open loan per computer, enforced in the
  database. Filters: overdue, student no longer enrolled.
- **Notices to families**: the adults who receive the school's notices get an email when
  the computer is lent (computer, dates, condition noted), seven days before the due
  date, and once if it is overdue, each in their own language and the school's layout.
  Each notice goes once.
- **Family portal**: each child's card shows the computer lent and its due date.

Lab computers need nothing here: a lab profile lets the student groups log in.

## Security

The school office (Administration) manages accounts and loans; staff read them and cannot
reach the directory. The office sees the loan computers of its companies, and nothing
else of the fleet; the disk passphrase and the machine token stay out of its reach.

The directory configuration (address, token, student group, folder) is for system
administrators only: whoever sets the address chooses where the token is sent, and
whoever sets the student group chooses which group every student joins. A new address
drops the old token. The token is stored encrypted with bf_policy's escrow key and never
shown again. An account's directory id, user name and synchronisation state are written by
the system only, at creation as on any change: the office chooses the student and the
school, never which directory user the account drives. A directory user outside the student folder is never adopted. A shown
password is deleted when closed, or at the next synchronisation after five minutes. Families have no
access right: the portal reads their own children's loans through their guardian links.

## What has been checked, and what has not

- Checked in QA against a real Authentik 2026.8.3 and sssd 2.13 (Fedora 44): accounts,
  groups, class change, departure, password; lab, loan, empty and closed seats; a
  suspended student is refused. Emails checked through a local SMTP catcher, not yet
  delivered to a real mailbox.
- Not yet tried: the Blue Fox OS install in a virtual machine.
- "Wipe the session at logout" on a lab seat is not applied by Blue Fox OS yet (hidden).
- Reinstalling a lent computer gives it a new machine record: move the open loan to the
  new computer by hand (the loan's computer can be changed while it is lent).
- The LDAP login worked in QA with Authentik's default authentication flow (no
  second factor configured for the students). A school whose flow demands one needs a
  flow without it for the students' LDAP login.
- The accounts of students who left are suspended, never deleted.
