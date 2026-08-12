# bf_security_awareness — Security model (clawback & email ingestion)

This module can **send simulated lures** and, since v1.6, **pull a confirmed
malicious email out of every internal mailbox** (clawback / PhishRIP). Those are
high-impact capabilities; this note is the threat model and the controls.

## Capabilities & blast radius

| Capability | Who | Reversible? |
|---|---|---|
| Run/preview phishing simulations | `group_bf_secaware_user` (Opérateur) | n/a |
| Triage reported emails, **Aperçu** of a clawback | `group_bf_secaware_manager` | yes (read-only) |
| **Execute / restore** a clawback (move mail in every mailbox) | `group_bf_secaware_purge` | yes — both modes restorable |

`group_bf_secaware_purge` ⊃ `manager` ⊃ `user`. Previewing and actually purging
are **separate grants** on purpose: a manager can investigate without being able
to remove mail from everyone's inbox.

## Controls in place

- **Least privilege (Odoo side).** Execute/restore require `group_bf_secaware_purge`,
  enforced both in the action methods (`_check_purge_rights`) and on the buttons.
- **Reversible removal.** `quarantine` moves matches to a hidden `Quarantaine BF`
  folder; `delete` moves them to each mailbox's Trash. **Both** are restorable to
  INBOX via *Restaurer* (delete stays recoverable until the mailbox purges Trash).
- **No fuzzy deletion.** `mode=delete` refuses the heuristic strategy — it requires
  an exact `Message-ID` (parsed from the reported `.eml`), re-asserted in `_run`.
- **Mandatory preview** before any heuristic (From/Subject) execution.
- **Blast-radius cap (all strategies).** Execution first establishes a live found
  count (running a search even for the `message_id` path); if it exceeds
  `clawback_max_blast_messages` (default 25), execution is blocked until a purge
  user clicks *Confirmer le rayon élevé*. `0` disables the cap.
- **Secrets at rest.** OAuth client secret and per-mailbox passwords are
  Fernet-encrypted; the key is read from `BF_SECAWARE_FERNET_KEY` (env) or
  `bf_secaware_fernet_key` (odoo.conf) — **never the database**. Secret fields are
  write-only in the UI (`********`). The OAuth access token lives in memory only.
- **TLS.** `imaplib.IMAP4_SSL` validates the certificate and hostname (Python 3.x
  default context).
- **No content stored.** Reports and clawback records keep only metadata
  (From / Subject / Message-ID / per-mailbox counts) — never email bodies. The
  forwarded `.eml` is kept as an attachment for evidence (see Retention).
- **Auditability.** Every preview/execute/restore is logged to the operation's
  and the report's chatter (operator, criteria, per-mailbox found/removed), with
  an optional ntfy alert on execute.
- **Email ingestion is internal-only.** The reporting alias uses
  `alias_contact='employees'` and `message_new` re-checks the sender is an
  internal user — external senders are rejected. This blocks triage spam/DoS.
- **IMAP-injection safe.** Every value reaching an IMAP `SEARCH`/`CREATE`
  (Subject / From / Message-ID, from a `.eml` or a typed report) is screened for
  control characters in `_imap_quote`, so a CR/LF cannot break out of the quoted
  argument and inject a second IMAP command.
- **Execution gated by a flag, not by state.** A clawback only ever moves mail
  when `authorized=True`, which is set *only* inside `action_execute` under purge
  rights and past every rail; `write()` forbids non-purge users from setting it,
  and the resume cron only acts on authorized ops. `_run` re-asserts the
  delete/Message-ID rail. So state cannot be used to smuggle an unvetted purge.
- **Multi-company isolation.** Record rules scope connectors, operations and
  reports to the user's company.

## Deployment-side controls (operator responsibility)

- **Scope the M365 app.** App-only `IMAP.AccessAsApp` / `Mail.ReadWrite` is
  tenant-wide by default. Restrict the service principal to a mailbox security
  group via an Exchange **Application Access Policy** (`New-ApplicationAccessPolicy`).
  Rotate the client secret; keep it short-lived.
- **Protect the Fernet key.** Store it in the environment / odoo.conf with tight
  file permissions; rotate on suspected exposure (re-enter secrets after rotation).
- **Grant `group_bf_secaware_purge` to as few people as possible.**

## Residual risks / roadmap

- **Report alias trusts the envelope `From`.** `message_new` resolves the sender
  from the (unauthenticated) `From:` header, so a spoofed internal address can
  inject a *report* record (it can **never** auto-trigger a clawback — that stays
  a manual, previewed, purge-gated action). The proportionate control is at the
  MTA: **the alias must sit behind SPF/DKIM/DMARC enforcement for the internal
  domain**, and the in-app *Rapporter un courriel suspect* wizard (authenticated)
  is the trusted path. Treat the open alias as a convenience, not a trust anchor.
- **`.eml` retention (Loi 25).** Forwarded originals contain the full message
  (and any malicious attachment, stored — not executed — in the filestore). A
  retention cron that purges/anonymizes `.eml` attachments and old behavioural
  data after a window is tracked in `ROADMAP.md` (v1.3 privacy item).
- **Provider without app-only admin IMAP.** Use the `imap_password` connector
  mode (one app password per mailbox) rather than `m365_oauth`.
