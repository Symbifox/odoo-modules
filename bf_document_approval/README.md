# Documents — Multi-approver Sign-off

Companion to `project_knowledge_matrix`. A policy is published only once
everyone who had to weigh in has done so, and its distribution follows the
RACI already recorded in the knowledge matrix.

## Why

`project.document.version` already carried an approver, an approval date and a
`draft → review → approved → released` cycle. One approver — and `action_release`
approved on the spot when nobody had:

```python
if not version.approved_by_id:
    vals['approved_by_id'] = self.env.user.id
```

A policy could therefore be published in a single click without anyone having
weighed in, and the acknowledgement requests went out behind it. Locking
`action_approve` alone would have looked right and left the door wide open, so
the guard sits on **both** entry points.

## Features

- **Named approvers** on a version (`project.document.approver`), each with a
  verdict, a timestamp and a reason. An approver can be marked *not required*:
  their opinion is sought, but it does not block.
- **The lock**: while a required verdict is missing, the version can be neither
  approved nor released, and the refusal names who is still expected. A
  rejection blocks and shows its reason. A rejection *without* a reason is
  itself rejected — a rejection nobody can act on helps nobody.
- **No named approver means nothing changes.** An organisation with no round
  table to hold is not forced to invent one, and existing documents keep
  behaving exactly as before.
- **Distribution follows the RACI**: once released, the stakeholders marked
  *informed* on the document's knowledge-matrix items get their
  `project.document.distribution`, with the acknowledgement and optional
  signature that model already handles. No second recipient list is kept — a
  copied list is a list that goes stale.

## What it deliberately does not do

No conditional tiers and no delegation: OCA's `base_tier_validation` does that
well. It is licensed AGPL-3, which is incompatible with redistributing this
suite, and the need this module answers — a named round table that has to
conclude before a policy goes out — does not call for a tier engine.

## Models

| Model | Purpose |
|---|---|
| `project.document.approver` | One person's verdict on one version |
| `project.document.version` (extended) | `approver_ids`, the release guard, RACI distribution |

## Tests

`odoo -d <db> -u bf_document_approval --test-enable --test-tags /bf_document_approval`

Ten tests covering the lock on both entry points, non-required opinions,
rejection with and without a reason, and the RACI distribution. Each one has
been shown to fail when the behaviour it covers is broken.

## Licence

Business Source License 1.1 — see [LICENSE](LICENSE). You may run it for your
own internal business operations; providing it to third parties as a hosted,
managed or resold service requires a written agreement. It converts to
LGPL-3.0-or-later on 2030-08-18.
