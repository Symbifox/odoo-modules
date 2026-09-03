# Document History (`bf_attachment_version`)

`ir.attachment` keeps no history. When an office editor saves, when a robot
drops a new build of a deliverable, or when an API call rewrites `raw`, the
previous content is gone: no trace, no way back.

This module keeps the previous state. Every time the **content** of an eligible
attachment is replaced, it creates a `bf.attachment.version` record pointing at
a retention attachment that holds the bytes from before.

## Why the content lives in another attachment

Odoo's filestore addresses files by their SHA-1 fingerprint, and its garbage
collector keeps any file still referenced by a row in `ir_attachment`. The
retention attachment therefore reuses the file already on disk: **no extra bytes
at snapshot time**, only a database row. Storage grows on the day the content
truly diverges, which is the day worth paying for.

`test_le_fichier_conserve_survit_au_ramasse_miettes` actually runs the filestore
garbage collector after an overwrite, because that is precisely where this
design could lie without saying so.

## What gets versioned

A binary attachment, with no `res_field` (so never the storage backing a binary
field), whose extension is in the configured list, whose model is not excluded,
and whose incoming content genuinely differs from the previous one.

A rewrite with identical content creates nothing. Without that check,
`force_storage` would manufacture one version per attachment in the database.

## Access

A version, and the content it preserves, are visible only to whoever can read
the original attachment. `bf.attachment.version._search` filters the matched
rows through `ir.attachment`'s own permissions, and the retention attachment
carries `res_model = 'bf.attachment.version'`, so its access runs back through
the same rule.

## Settings

Settings → Document History. Everything lives in `ir.config_parameter` under the
`bf_attachment_version.` prefix: `actif`, `extensions`, `modeles_exclus`,
`max_versions`, `max_jours`, `taille_max_mo`. A daily scheduled action applies
the retention ceilings across the whole database, not just the last write.

## Restoring

Restoring goes through a normal `write`, so it creates a version of whatever it
replaces. Stepping back loses nothing either.

## Bypass context

`with_context(bf_sans_version=True)` disarms the hook for one write. The module
uses it itself when writing its own retention attachments.

## Origin

`onlyoffice`, `collabora`, `interface` or `autre`, inferred from the HTTP path of
the current request. Neither office connector is ours, so neither can be asked to
set a flag, but the path is an observable fact.

## Tests

35 tests, including an adversarial pass: each guarantee was removed from the code
in turn and the suite went red every time.

## Licence

BUSL-1.1, converting to LGPL-3.0-or-later on 2030-09-02. Internal production use
is free; providing it as a product or service to third parties requires a
separate agreement.
