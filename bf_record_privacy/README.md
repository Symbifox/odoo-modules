# Record Privacy Guard (`bf_record_privacy`)

Closes two leaks of Odoo 18 that hold for every model whose records are hidden
from some users: personal notes, mailboxes, private conversations, expenses,
invoices, tasks of a private project. Nothing to set up: install it and both
gates are closed.

Labels below are quoted as they appear in French (fr_CA); the module ships English
sources and a fr_CA translation.

## The record name in debug mode

Odoo 18 gives the "debug mode" group to every internal user, and anyone can open
`/odoo?debug=1`. In debug mode, the error raised by a record rule listed up to six
refused records by their name. Reading a guessed id was enough to learn the
subject of a colleague's email or the title of a private note.

The error now keeps the model, the id and the rules at fault, and replaces the
name with « nom masqué ». Administrators (Settings group) still see the names, to
diagnose. Outside debug mode, the error is unchanged.

## Follower lines

Follower lines (`mail.followers`) were readable by every internal user, with no
rule: a search listed who follows records the reader cannot open, and so which
records exist. A line is now read only by someone who can read the record it
follows, administrators included, the way Odoo already treats messages and
attachments. Two gates are covered: the search (which also serves the read of a
line by its id) and the access check that controllers call.

Unchanged: the chatter of a record you can read, subscriptions, notifications,
and everything that runs as superuser. A few core helpers read followers under the
name of the person acting (Project copies the followers of a parent task to a new
subtask, and of a project to its copy): they now skip the lines of records that
person cannot read.

The subtypes of a follower line (`/mail/read_subscription_data`) are only served
to someone who can see the line; the refusal names neither the line's model nor
its record.

## Limits

- Only the record name is hidden in the error. A name read as superuser elsewhere
  (a many2one shown on a record you can read, for instance) is the business of
  each module; Healthy Fox, Personal Budget and Credit & Identity do it for
  their records.
- Only reading follower lines is filtered. Internal users have no other right on
  them. Administrators keep the right to write and delete lines, but deleting a
  line (and changing its record or partner) reads it first: code that cleans
  follower lines for everyone, such as a scheduled action running as the
  administrator, must do it in sudo, as Odoo's own code does. Blue Fox's follower
  cleanup (`bf_follower_cleanup`) does from 18.0.2.2.1 on.
- A follower line id can still be tested for existence (`exists()`).
