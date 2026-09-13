# People org chart (`bf_org_chart_people`)

One **Reports to** field on the contact form, and the chain of command draws
itself.

## Why another field

Odoo's `parent_id` already means "works at". Repurposing it to mean "reports
to" has a measured cost: giving a contact a parent **overwrites its address**
with the parent's, silently, even when the contact is flagged as a company,
because the copy is triggered by `type == "contact"`, which is the default.

| Write | Address afterwards |
|---|---|
| `{"parent_id": holding}` | **overwritten** |
| `{"manager_id": boss}` | untouched |

## What the module adds

* `manager_id` (Reports to) and its inverse `subordinate_ids`.
* A cycle guard: nobody reports to themselves, however far up you walk.
* A warning, **not a refusal**, when the manager works for another company. A
  group that provides shared services is a real structure, and the plant IT
  lead who answers to the group IT director is not a typing mistake.
* Odoo 18's native **hierarchy view** (`web_hierarchy`) on the new field: fold,
  unfold, drag a card to change who it reports to. No JavaScript of our own.
* The engine's drawing, for the printable page and the PDF.

## What gets drawn

From a company, the chart takes every person at that company, plus the outside
managers it needs to keep the chain standing. Those are tinted, and their box
carries their employer's name.

From a person, it is the same chart: their company's.
