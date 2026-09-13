# Contact absences from a hand-kept calendar (`bf_contact_absence_calendar`)

Before there is a module, people note their clients' holidays somewhere. Often
that somewhere is a hand-kept Nextcloud calendar. This bridge reads it.

Depends on `bf_contact_absence` and `bf_contact_absence_mail`.

## The two shapes it understands

A real hand-kept calendar carries both, and both matter:

- **a lone marker** on the day of departure, "First name - Company" or
  "First name (Company) - holiday". The period has no end: it is proposed
  without a return date, to be completed in one gesture;
- **a pair** that brackets it, "Holiday - First name Last name" then
  "Back from holiday First name". The period runs from the departure to the day
  before the return.

Three things only showed up on real data:

- 🔴 **The return entry almost never repeats the surname.** Matching the two
  halves on the exact name left every period open-ended. The pairing works on a
  prefix instead.
- 🔴 **The word naming the nature can be a suffix** as much as a prefix. Read as
  a company hint, it blocked matching altogether.
- 🔴 **A return is consumed once, and only within reach.** A single "Back from
  holiday X" closed three separate departures of the same shorthand, one of them
  fourteen months old. A return now closes the departure it most closely
  follows, and nothing more than seventy days back.

## Shorthands are taught, never guessed

A hand-kept calendar uses its keeper's shorthands: initials, a nickname, the
name of a file rather than of a person. Initials resemble no name, and matching
them against a name that merely starts alike would be exactly the mistake this
module refuses elsewhere. So the shorthands are a list you fill in, one line at
a time, and the module remembers them.

## What it does not do

It **writes nothing** back to the calendar and does not pull it into Odoo's own
agenda: the calendar stays its keeper's notebook.

It creates **no contact**, and refuses an ambiguous match. An entry it cannot
recognise is **reported with its title**, not guessed: calling the wrong David
would send one client's mail to another.

And like everything machine-sourced here, it **proposes**: a person accepts or
rejects.

## The connection

No new credentials: the module reuses the calendar synchronization
configuration already in place, meaning the server address, the account and the
application password already encrypted in the database.

⚠️ **It does not depend on it, though.** The module that carries that
configuration requires the Google client library, which not every image ships:
depending on it would make this bridge **uninstallable** where the library is
missing, for the sake of an address and a password. The configuration is looked
up in the registry at run time, and the module says so plainly when it is not
there.
