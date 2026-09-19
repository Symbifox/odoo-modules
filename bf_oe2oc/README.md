# Enterprise migration recovery (`bf_oe2oc`)

The Odoo end of an Enterprise → Community migration. The other end is
[`odoo18-ee2ce`](https://github.com/Symbifox/odoo18-ee2ce), which imports the
dump and writes, alongside it, everything it could not place.

## The problem

A Community database has no Enterprise `helpdesk_ticket` table, no
`knowledge_article`, no `sign_template`. The import tool skips those rows
because there is nowhere to write them, counts them in its summary, and that is
the end of it: the data goes when the dump goes.

Since version 0.4.0, `odoo18-ee2ce` writes those rows to
`enterprise-leftovers.json` instead. This module brings them back in.

## Two things, in this order

**Keep.** Every table in the file becomes a browsable, searchable record set,
whether a mapping exists for it or not. Before deciding where a ticket belongs,
you have to still have it.

**Re-home.** Four mappings ship with the module:

| Enterprise table | Target model | Module |
|---|---|---|
| `helpdesk_ticket` | `helpdesk.ticket` | `helpdesk_mgmt` (OCA) |
| `knowledge_article` | `document.page` | `document_page` (OCA) |
| `sign_template` | `bf.sign.field.template` | `bf_sign` |
| `sale_subscription_plan` | `contract.template` | `contract` (OCA) |

Each one states **what it cannot carry** before anything is written, and a
preview shows the first ten rows as they would be created, with the list of what
was dropped and why.

A mapping whose target model is not installed here does not block the batch:
that table is set aside, by name, and the rest goes through.

## What the module does not take on trust

**Identifiers.** The import preserves primary keys, so an Enterprise
`partner_id` usually still points at the right contact. Usually is not always,
and a `stage_id` pointing into an Enterprise-only table points nowhere. Every
reference is checked against the target model and dropped, with its reason, when
the record is not there.

**Selections.** A value is kept only if the target field offers it.

**The schema.** Mappings apply to the columns actually present in the file. An
unknown column stays on the row rather than being invented or thrown away.

**A row holding together.** Each record is created in its own savepoint: a row
that fails is logged and does not cost the others.

## The post-migration checks

The import tool prints a list of things to finish, then exits — and the list
lives exactly as long as the terminal scrollback. The same checks run here,
against the live instance.

| Check | What it catches |
|---|---|
| Language or timezone codes that do not exist | The worst of the two: Odoo **raises on every call** that resolves them, and the traceback names the code, not the record |
| Languages used but inactive | Translated fields silently falling back |
| `web.base.url` after neutralization | Email links pointing at the recipient's own machine |
| Scheduled actions still stopped | Follow-ups and recurring invoices asleep |
| Sequences behind their table | A duplicate key error on the next record, days later |
| Attachments whose file did not come along | A filestore copied to the wrong level |
| Active outgoing mail servers | An imported mail queue going out to real recipients |
| Enterprise data set aside | A leftovers file never uploaded, or never dealt with |
| Restore the dbfilter and the master password | By hand: those settings live in `odoo.conf`, out of reach |
| Review the users and their access | By hand: passwords come across, authentication tokens do not |

A check distinguishes three answers, and they do not mean the same thing:
**nothing to do** (it looked), **to fix** (it found something), **not
verifiable** (it could not look — and that shows, rather than counting as a
pass).

## How to use it

1. Migrate with `odoo18-ee2ce`. Phase 9 writes `enterprise-leftovers.json`
   next to the dump.
2. Install `bf_oe2oc` in the migrated instance.
3. **Enterprise import › Imports**, upload the file, *Read the file*.
4. Table by table: *Preview*, then *Re-home*. Or *Re-home everything that has a
   mapping* once you have been through the previews.
5. **Enterprise import › Check everything**, and deal with what comes up.

## Access

A single group, **Import management**, implied by system administration.
Re-homing writes into business models from an uploaded file, and the checks read
the instance configuration: this is not a right to hand around.

## Adding a mapping

Declare a class in `models/handlers.py` — source table, target model, label,
what does not follow, and the column → field map — then add it to the `HANDLERS`
dict. The form picks it up with no other change.

## Tests

55 tests, including a full round trip over a real 18-table, 915-row file, and three that read the wording of a finding rather than its state: a check can return the right verdict in the wrong French.

```bash
odoo -d <database> -u bf_oe2oc --test-enable --test-tags /bf_oe2oc \
     --stop-after-init --no-http --log-level=test
```

## Requirements

Odoo 18.0 Community. Depends on `base` and `mail` only — the four target modules
are detected at runtime, so the module installs and is useful without any of
them.

## Licence

LGPL-3. See [LICENSE](LICENSE).
