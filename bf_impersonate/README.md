# Impersonate a User (`bf_impersonate`)

See Symbifox exactly as one of your users sees it: their menus, their access
rights, their preferences and language, their data. Support staff use it to
understand "I cannot see the pipeline" without guessing, and, when allowed, to fix
something in the person's place.

Every session has a reason, a time limit and a journal entry that no access right
can rewrite. The person can be notified. Nothing is ever sent in their name.

Labels below are quoted as they appear in French (fr_CA); the module ships English sources and a fr_CA translation.

## Two rights, granted by hand

| Group (category « Incarnation ») | What it allows |
|---|---|
| « Lecture seule » | see Symbifox as another internal user; nothing they do is kept |
| « Lecture et écriture » | also act as that user; every change is journaled |

Nobody gets either right at installation, administrators included, and neither
does the `admin` account, which is often shared.

## Opening a session

From the avatar menu (« Voir Symbifox en tant que quelqu'un d'autre ») or from a
user's form (« Voir Symbifox en tant que cette personne »). The assistant asks for
the person, a reason (the person can read it), a duration and, with the write
right, the mode. The web client then reloads on the person's home page, in the
person's language and time zone.

Refused at the door:

- yourself, the superuser, inactive and portal users;
- accounts ticked « Compte protégé contre l'incarnation » (technical accounts:
  integrations, assistants);
- administrators, unless the setting allows it;
- for someone who is not an administrator: anyone with an access right they do
  not have themselves. A support person never gets access rights they do not
  already have; they do see the person's own records (mailbox, notes, Gen
  conversations), which is why the reason, the journal and the notice exist.

A bar stays at the bottom of every page, on a phone too: who you are seeing
Symbifox as, the mode, the time left, and « Revenir à mon compte ». Other tabs of
the same browser are covered at once and reload a few seconds later: they share the
session, and a tab left open would otherwise show your own account while acting as
the person. The reloads are spread out on purpose: two tabs starting in the same
second trip over each other in Odoo's guided-tour service, whose state lives in the
shared browser storage.

## Read only

Explicit writes are refused with a message that says why: saving a form, any
button, posting a message, uploading a file, reordering. A button method that
the web client calls directly (`action_…`, `button_…`) counts as a button; only
navigation methods (`action_open…`, `action_view…`, `action_get…`,
`action_show…`, `action_download…`), which return a screen or a file, run. Two calls that
a screen makes on its own when it shows a record (marking an email as read,
noting that a bookmark was used) are answered "done" without running, so the
person does not find their emails marked as read by you; clicked as a button,
the same method is refused like any other. Saving a wizard (a
transient model) only changes what is on screen, so it runs rolled back like a
read; a button that Odoo flags as read only (`@api.readonly`) passes.

Everything else runs **in a rolled-back savepoint**: the request is served, then
every database change it made on its cursor is undone, including changes made
with `sudo()`.
Dashboards and counters of in-house modules call read methods that Odoo does not
flag as reads; a list of allowed methods would never end, and a list of forbidden
models leaks, because many Odoo writes go through `sudo()` after an access check.
Callbacks registered for after the commit are dropped with the rest, and an
explicit `commit()` during the request is refused.

Not covered by the rollback: a network call that a read method makes on its own
(IMAP, an external API), a module that opens its own database cursor and commits
on it, and files a request writes to the filestore (attachments whose records are
rolled back; the filestore cleanup removes them). Outgoing messages are refused
anyway, see below.

## Read and write

The person's own record rules keep working: what you create is theirs. Each request
that writes is journaled with what really changed (model, operation, records,
fields), read from the ORM, not guessed from the method name, including the writes
that the triggered code makes with `sudo()`. Server actions are always journaled.
Messages posted always have **you** as their author, whatever author the call names,
and are linked to the journal entry; their body is never modified.

## Never, in either mode

- **Nothing is sent**: no email (`mail.mail`, direct SMTP; a queued email
  cannot be changed either, nor the message it takes its subject, sender and
  attachments from, nor a message whose notifications are still scheduled), no text message (`sms.sms`, refused even in read
  only since a text message leaves at once; `message_post` with SMS numbers), no
  method whose name says `send`, no "Send later" message
  (`mail.scheduled.message`, neither created nor changed), and no message other
  than an internal note without recipients (no notice, no message to followers,
  no Discuss conversation, no `notification` message posted on purpose).
  Internal notes reach the internal followers subscribed to notes, in their Odoo
  inbox; tracking messages that Odoo posts by itself (a stage change, for
  instance) are allowed too, signed by you. If a follower receives notifications
  by email, the whole change is refused. Notifications that would wait for a cron
  (`mail.message.schedule`, which `mail_post_defer` uses for every notification)
  are sent at once instead, under the same rules.
- **Discuss calls**: joining or starting a call (`/mail/rtc/`).
- **Private data**, in both modes and for administrators too: models named
  `health.*`, the mood journal and any model that declares `_gen_scope`, the
  credentials vault (`project.credential`, whose passwords would outlive the
  session), and Gen conversations marked private or attached to a private record
  (`gen_private` and `gen_scope`, which exist from `bf_claude_chat` 18.0.1.36.0
  on; with an earlier version there is no such marker to read). The RPC
  entry point refuses them, and record rules hide them wherever access rules
  apply, including the messages, activities, followers, attachments and ratings
  attached to them (Odoo otherwise lets people re-read the notes they wrote on
  any record). An access error about them names no record.
- **Phone** (`bf_softphone`): every method that names the phone is refused. Its
  configuration carries the person's SIP secret, and a call placed during a
  rolled-back request still leaves on their line.
- **Pairing**: no phone, extension or OAuth account is paired in the person's
  name (`/auth/start`, `/auth/consent`, `/oauth/` routes and every `*.device`
  model): such a token would outlive the session.
- **Identity**: password, API keys, two-factor authentication, login, access
  rights, the protection flag of a user; the email address, phone and mobile of
  the contact linked to the person's user, where a password reset goes, whatever
  ORM path writes them (their contact, their user or employee record, a portal
  page, a nested command, code running as superuser; a page or a command that
  resends the same value passes, a direct call on their contact or user record
  is refused as soon as it names the field); relinking their user record to another contact, or to
  another employee record from the user side; merging contacts; creating or
  deleting a user; becoming the superuser; opening another impersonation.
- **Rights and configuration**: groups, access rights, record rules, models,
  system parameters, the Settings form, actions, scheduled jobs, automations,
  modules, companies.
  The assistant already keeps a non-administrator from seeing anyone with more
  rights than they have; this also holds when the setting allows seeing an
  administrator.
- **Gen** (`bf_claude_chat`): it acts through XML-RPC with the person's operator
  key, outside the session. Reading past conversations stays possible, except
  the private ones.
- **Sensitive models** (identity, Gen, OTP vault, signatures, SMS, newsletters
  `mailing.*`, devices, rights and configuration): only known reads and methods
  whose name starts with `get_`, `load_`, `read_`, `search_`, `systray_`,
  `count_`, `has_` or `is_` pass, in both modes. Any other method is refused, even
  in read only, since a rolled-back call may already have reached a provider's
  API, and a newsletter put in the queue would leave later from a cron. OTP seeds
  are encrypted in the browser and stay unreadable.

## The end of a session

« Revenir à mon compte », the time limit (checked on every request; the bar returns
on its own), logging out, or an administrator's « Mettre fin à cette session » on the
journal entry. When a session ends in the middle of an explicit write (saving, a button,
posting), that write is refused
rather than replayed under your own name. A cron closes, every 15 minutes, the
entries of sessions that ended without a request (time limit, closed browser).

## The journal

« Paramètres › Utilisateurs et sociétés › Incarnations »: who, as whom, why, the
mode, the planned and actual end, why it ended, the IP address, and the actions
taken. No access right allows writing to it, administrators included; only the
module's code writes, as superuser. The person seen reads their own entries, so does
the person who impersonated; administrators read them all. The IP address is shown
to administrators only.

## Settings

« Paramètres › Paramètres généraux », block « Incarnation »:

- « Aviser la personne vue »: never (journal only), at the start of each session
  (default), or at the start and then a summary at the end. The notice arrives
  through the person's own notification preference (inbox or email) and links to the
  journal entry. It leaves at once, never through the email queue or a delay: the
  start notice before the session begins.
- Default and maximum duration (30 and 120 minutes).
- « Permettre de voir Symbifox en tant qu'une personne de l'administration » (off).

## Privacy (Quebec, Law 25)

Article 20 of the private-sector Act limits access to personal information to
people who need it for their duties. Impersonating someone reaches their
information, their mailbox included: hence the mandatory reason, the journal nobody
rewrites, and the notice. On a client's instance, the provider's access should also
be provided for in the service agreement.

## Why not OCA `impersonate_login`

Read on its 18.0 branch in October 2026, before writing this module: its journal is writable by every
internal user; it rewrites the body of every message posted ("Logged in as X"), and
that text leaves in emails to customers; it rewrites `create_uid` on every model,
which breaks every "what I created" rule; it grants the right to the `admin` account
at installation; it has no reason, no time limit, no notice and no read-only mode.

## Limits

- An administrator who runs Python code (a server action with `sudo()`) can still
  alter the journal, and uninstalling the module drops it. The journal is safe from
  access rights, not from someone who can change the code.
- A network call that a read method makes on its own (IMAP, an external API) is not
  undone by the rollback.
- A module that opens its own database cursor commits on it: the read-only
  rollback does not reach those writes.
- An in-house transport that sends without `mail.mail` or `sms.sms` (a text
  message provider called directly, for instance) is caught only by the `send`
  rule and by the sensitive `sms.` models; a write-mode button that reaches it
  under another name is not intercepted. Webhooks of automations are not
  intercepted either (changing automations is refused).
- Changing a notification preference writes a group behind the scenes, so it is
  refused like any change of rights. The employee record's own fields (office
  phone, private details) stay editable: a password reset does not use them.
- A module that rewrites the person's contact address on its own (while a page
  loads, for instance) is refused too; if it catches that refusal, what it does
  next is up to it.
- Private data is hidden through record rules, which code running as superuser
  skips: a counter or any value computed with `sudo()` may still reveal it.
- Real-time notifications pushed on the bus are built as superuser and reach the
  person's open tabs, yours included while you see Symbifox as them.
- Other tabs are reloaded through `BroadcastChannel`; a browser without it leaves a
  stale tab until its next reload.

## Design notes

The module never extends `base` or `mail.thread`: Odoo re-initialises every model
that inherits them (columns, indexes, foreign keys) on each install and upgrade,
and a database with orphaned rows then refuses to install. The guards live on
`mail.message` and `mail.mail`, and the journal of writes wraps the ORM methods
of `BaseModel` without touching any schema (`orm_journal.py`). The same wrapper
refuses, whatever the path, a text message and any change to the person's
identity: the address on their contact, the rights and links written on a user
record, and creating or deleting users. Other rights tables (groups, access
rules) are refused at the RPC entry point.

## Changelog

- **18.0.1.0.4**: the notice to the person is always sent at once, in a context of its own: the browser can no longer delay it or leave it to a queue. The start notice goes out before the session switches; sent after the commit, it went out under the person and the sending checks refused it on databases without `mail_post_defer`. The message of an email still in the queue cannot be changed either.
- **18.0.1.0.3**: the address and phones of the person's contact are protected
  where the ORM writes, whatever the path (user or employee record, portal page,
  nested command, superuser code), and so is the contact their user is linked
  to; merging contacts and deleting a user are refused; in read only, downloads
  count as navigation and two automatic "mark as" calls are answered without
  running; private data (health, mood journal, models declaring
  `_gen_scope`, the credentials vault, private Gen conversations, and what is
  attached to them) is hidden in both modes, and access errors about it name no
  record; "Send later" messages (created or changed), queued emails, text
  messages, methods that say `send`, newsletters, Discuss calls and the phone are
  refused; button methods called directly count as buttons; deferred
  notifications are sent at once under the same rules; pairing a phone, an
  extension or an OAuth account and the Settings form are refused in both modes;
  on sensitive models only reads and read-prefixed methods pass, even in read
  only; the journal's IP address is for
  administrators only; the start date is labelled correctly on a fresh install.
- **18.0.1.0.2**: after an independent adversarial review. In write mode, rights
  and configuration are never changed, the person's email and phone numbers are
  protected, a message's author is always the impersonator, and writes made with
  `sudo()` and server actions are journaled. In read-only mode, an explicit
  commit is refused during the rollback. The write journal fails open.
- **18.0.1.0.1**: no more `base` or `mail.thread` inheritance (an install on a
  tenant with orphaned tracking rows failed on a foreign key); guards moved to
  `mail.message` and `mail.mail`; other tabs are veiled at once and reload with a
  spread-out delay.
- **18.0.1.0.0**: first version.
