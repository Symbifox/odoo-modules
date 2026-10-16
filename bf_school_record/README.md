# Symbifox École: student's file (`bf_school_record`)

The file a private school keeps for each student, the register of enrolment, the transfer to the
school that receives a student, and the copy given to the person it concerns.

## Features

- **The file**: one per student and school, opened by the first enrolment. It lists the eight pieces
  of the Regulation respecting private educational institutions at the preschool, elementary and
  secondary levels (E-9.1, r. 3, s. 7): which are there, which are missing, which do not apply and
  why. Five are always required; three apply "le cas échéant" and are marked received, missing, or
  not applicable with a note.
- **The register of enrolment** (s. 8): name, residence address, birth date, language of
  instruction and holder of parental authority, read from the file, on screen and as a PDF.
- **Transfer** (Private Education Act s. 38.1, added by Law 23): the school that loses a student
  sends the new one, as soon as possible, the information it needs. The law gives no figure, so
  the school sets its own target (days); the office chooses what goes (guardians, results, pieces
  of the file: off by default) and the package carries a manifest with the SHA-256 of each file.
- **Portability** (P-39.1 s. 27, A-2.1 s. 84): a structured JSON copy of what the school holds.
- **Closure**: one zip with the register and every file, for the Minister if the school closes
  (E-9.1, r. 3, s. 7 and 8).
- **Leaving**: "Student leaves" closes the file, asks for the notice of departure and ends the
  student's enrolments in that school, so the student is out of the group's averages, ranks and
  declarations. Reopening the file takes back the request for the notice, not the enrolments (the
  student is enrolled again in a group). A student who only changes group is not "leaving": nothing
  happens to the file.
- **Access log**: who created, replaced, downloaded or exported what. Nobody edits it.

## Security

The file is the administration's: teachers and families have no right on it. The download buttons
(piece, transfer package, copy of a file, closure export) give an address signed for the user who
clicked, for that file, for a few minutes: an address copied from elsewhere opens nothing and writes
nothing in the log. Each of them, and the standard download routes of the binary fields
(`/web/content`, used by the piece's upload widget), write a line in the access log; the closure
export, the copy of a file and the transfer package are built when downloaded, and kept nowhere a
teacher can read. The module's own download routes ask that nothing be kept by a cache. Reading a field through the programming
interface (a read of a piece's file by a member of the office) is not a download and is not logged. The line of a
download by a standard route is written by a cursor of its own, so that a read-only route (a replica) raises no
error and runs once.
A transfer's state and package move with the buttons, by RPC too; the package is compared with what
the file says today when the transfer is marked as sent, and a stale one must be prepared again; how a
transfer was sent can be corrected after the sending and the history keeps what it said. The package is
compared with its SHA-256 each time it leaves: one that no longer matches is not given out and the log says
so ("File refused"). The files under a piece, a package or a report card's PDF are not replaced or deleted
by hand (`ir.attachment` by RPC): they change by the screen or the button of their record, which keeps a
trace. A student's file is never deleted. In the exports, the register's cells never start a spreadsheet formula and a piece's
name stays inside its folder of the archive.

Several companies in one database are kept apart (files, pieces, transfers, log). The eight kinds of
pieces of the regulation ship with no company: every school reads them and **only the system
administration changes them**, since changing one changes which files are complete in every school;
a kind a school adds carries its company and counts for its files only. There is no separation
between schools of one company: the administration of a company sees all its schools' files.

## What has not been confirmed

- The scope of "the information it needs" in a transfer is not defined by the law: the school
  chooses it for each transfer.
- No retention schedule: the school's schedule for the destruction of files is not modelled.
- Law 23's designated deposit system for student information (M-15 s. 6.1): no order of
  designation has been found, so nothing is sent to one.
