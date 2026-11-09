# Secure Transfer: Privacy Bridge (`bf_securetransfer_privacy`)

[Secure Transfer](../bf_securetransfer) (`bf_securetransfer`) destroys client
files on a schedule: the `_cron_purge_expired` job deletes the object storage
files of expired transfers, and `_cron_gc_drafts` collects abandoned drafts.
This bridge records each of those destructions in the destruction register of
[Privacy Consent](../privacy_consent) (`privacy_consent`), the register that
Quebec's private sector privacy act (Law 25, section 3.2) asks an organisation
to keep.

- **Version**: `18.0.1.2.2`, for Odoo 18 Community.
- **Licence**: **BUSL-1.1**: production use allowed for your own internal
  business operations; providing the module as a product or service to third
  parties (hosted, managed or resold) requires a written agreement. Converts to
  **LGPL-3.0-or-later** on the Change Date stated in [`LICENSE`](LICENSE).
- **Languages**: English source, French (Canada) catalogue included. The
  retention rule and the retention policy this bridge delivers are stored
  in one language by `privacy_consent`, whatever the reader's: on a
  French-speaking company they are written in French at install and on
  upgrade, unless someone has already adapted them. The purpose summary
  is a translated field like any other text, and its French comes from
  the catalogue.
- **Installation**: automatic when both `bf_securetransfer` and
  `privacy_consent` are installed. The secure transfer works perfectly well
  without it.

## What it does

1. **It records, it does not destroy.** The purge already exists, deletes for
   real and is proven. The bridge hooks in behind it and writes to the
   register what it has just done: one immutable, hash-chained entry per
   purge, naming the transfer and its files.
2. It declares the file delivery purpose in the register of processing
   activities, with its legal basis and its retention rule.
3. It makes `secure.transfer` and `secure.transfer.file` classifiable, through
   the `_privacy_classifiable_models()` extension point.

## Design choices

- **Record what was destroyed, not what was meant to be.** The list of files
  is frozen BEFORE the purge runs: the purge moves them to the `purged` state,
  and afterwards nothing tells what this pass deleted from what an earlier pass
  had already cleaned up.
- **A purge that partly fails writes nothing.** When storage keys resist, the
  purge returns `False` and leaves the transfer as it is: the register must not
  say "destroyed".
- **Partial scope.** The transfer record (name, recipients, access log, dates)
  is kept; only the uploaded files leave the storage. The entry says so
  (`destruction_scope = partial`, the destroyed elements named) whenever the
  register offers these fields; on a register release without them, the entry
  is recorded as before.
- **The declared method is `delete`, not `secure_wipe`.** Objects are removed
  from the provider's namespace, with no guarantee about the underlying
  blocks: claiming a secure wipe would overstate it.
- **One language per register.** The entry is written in the company's
  language, whoever or whatever triggered the purge.
- **The retention code is an identifier, not a label.** The rule this
  bridge delivers is `ST-CONTENU`, in every database and in every
  language: the register cites it and a verifier looks it up here, so it
  is not translated. Releases 18.0.1.2.0 and 18.0.1.2.1 declared
  `ST-CONTENT`; a database installed in one of them is brought back by
  the 18.0.1.2.2 migration, and its register entries follow the rule,
  being attached to it by id.
- **Recording never blocks the purge.** If writing the entry fails, the purge
  stays successful and the error is logged.

## Extending the entry

Another bridge that knows more about a transfer (where its files came from,
which categories of personal information they carry, which retention rule
governs them) overrides `secure.transfer._privacy_destruction_vals()`. It runs
before the entry is created, in the register's language, and returns the
values the entry will hold: the register is immutable, there is no second
chance. [Document Requests: Privacy Bridge](../bf_document_request_privacy)
does exactly that.
