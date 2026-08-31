# Blue Fox Corporate Governance

The minute book of a share corporation, kept in Odoo: resolutions, the director
and officer registers, and a compliance calendar that reminds you before the
deadline rather than after.

*Developed and maintained by [Blue Fox Inc.](https://bluefoxconsultant.com)*

> **Extracted from `project_knowledge_matrix` 18.0.12.0.0.** The models, their
> tables and their external IDs were **reassigned**, never recreated. A database
> that already held resolutions finds them unchanged, sequence numbering
> included. See [Upgrading from project_knowledge_matrix](#upgrading-from-project_knowledge_matrix).

## Overview

Built for Quebec corporations under the LSAQ (*Loi sur les sociétés par
actions*), but nothing in it is province-specific beyond the wording of the
default compliance events.

- **Resolutions** — board and shareholder, with a lifecycle (Draft → Proposed →
  Adopted / Rejected / Superseded) and a branded PDF ready to sign
- **Signature blocks** that say who signs *and in what capacity*, rather than
  guessing it from a register
- **Director register** with appointment and end dates, end reason, linked
  resolutions, and domicile for the LSAQ Canadian-residency requirement
- **Officer register** with appointment history
- **Compliance calendar** with a daily reminder pass
- **Minute book** — a filtered view over the documents of
  `project_knowledge_matrix`, classified by minute-book section
- **Dashboard row** added to the knowledge dashboard, and five figures added to
  its biweekly email report

## Features

### Resolutions

| Type | Description |
|------|-------------|
| Board resolution | Standard board resolution |
| Shareholder resolution | Standard shareholder resolution |
| Written board resolution | Written resolution in lieu of a meeting |
| Written shareholder resolution | Written shareholder resolution |

Subject categories: officer appointment, director election, dividend
declaration, share issuance, bylaw amendment, contract approval, bank
authorization, auditor appointment, fiscal year, financial approval,
dissolution, other.

A reference is drawn from the `corporate.resolution` sequence (`RES-YYYY-NNN`)
on creation. Votes for / against / abstaining are recorded, along with an
"adopted unanimously" flag, the mover and the seconder.

### Who signs, and as what

The printed resolution reads its signature block in three steps, in order:

1. **Signatory lines**, when any are entered — they win, with the capacity
   typed on each line;
2. otherwise, for a **board** resolution — the directors **in office on the
   date of the meeting**, signing as directors;
3. otherwise — the mover and the seconder, **named without a capacity**.

Step 3 is deliberate. The module keeps no shareholder register: it knows who
carried the resolution, not the capacity in which that person signed it.
Printing a guess under a name is worse than printing a name.

Step 2 reads *at the date of the meeting*, not today. A resolution is often
printed months or years after the fact, and "who is a director now" would name
people who had not yet been elected.

### Compliance calendar

Five default events ship with the module: REQ annual declaration, annual general
meeting, director election, auditor appointment, financial approval. A daily
scheduled action creates an activity for every corporate manager when an event
falls due within 30 days, with a priority that rises as the date approaches.

### What it adds to the knowledge dashboard

A governance row — registers and compliance — appended through the
`project_knowledge_matrix.DashboardExtraRows` extension point rather than by
patching the base template around it.

The row is **absent**, not zeroed, when the dashboard is filtered by project.
These figures are company-wide; rendering them under a project filter would show
the same numbers for every project, which reads as a count *of* that project.

The biweekly email report gains five figures, each linked to a drill-down action
whose domain reproduces exactly the number that was clicked.

## Installation

1. Copy `bf_corporate_governance` next to `project_knowledge_matrix` in the
   addons path
2. Update the apps list
3. Install **Blue Fox Corporate Governance**

Dependency: `project_knowledge_matrix` (which brings `project`, `mail` and
`bf_onboarding_base`). No external Python packages.

## Upgrading from project_knowledge_matrix

Databases that carried the subsystem inside `project_knowledge_matrix` must
take this module **in the same run** as the base module's 18.0.12.0.0:

```bash
odoo -d <db> -u project_knowledge_matrix -i bf_corporate_governance \
     --stop-after-init
```

The base module's `pre-migrate` pass reassigns every corporate external ID —
declared records, and the ones Odoo generates by reflecting the code — from
`project_knowledge_matrix` to `bf_corporate_governance`, *before* the base
module loads. Without it, Odoo would drop the five models at the end of the
load, as records the updated module no longer names.

The pass refuses to run rather than lose data:

| Situation | What happens |
|-----------|--------------|
| This module present | External IDs reassigned; tables untouched |
| Absent, no corporate data, nothing extends the models | The empty tables are removed cleanly |
| Absent, data present | The upgrade stops with the record counts |

Modules that extend `corporate.resolution` — at Blue Fox, `bf_sign_corporate`
and `bf_gamification` — must depend on **this** module, not on the base one,
and be upgraded in the same run.

## Data Model

### corporate.resolution

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Resolution title |
| sequence | Char | Auto-generated reference (RES-YYYY-NNN) |
| resolution_type | Selection | board / shareholder / written_board / written_shareholder |
| meeting_type | Selection | regular / special / agm / written |
| subject_category | Selection | 12 categories |
| status | Selection | draft / proposed / adopted / rejected / superseded |
| meeting_date | Date | Date of the meeting or of the written resolution |
| whereas_text | Html | ATTENDU QUE preamble |
| resolved_text | Html | IL EST RÉSOLU QUE body |
| mover_id / seconder_id | Many2one | res.partner |
| vote_for / vote_against / vote_abstain | Integer | Vote counts |
| unanimously_adopted | Boolean | Unanimous adoption flag |
| effective_date | Date | When the resolution takes effect |
| superseded_by_id | Many2one | Superseding resolution |
| signatory_ids | One2many | Signature block lines (copied with the record) |
| document_ids | Many2many | project.document — the minute book |
| company_id | Many2one | Company |

### corporate.resolution.signatory

| Field | Type | Description |
|-------|------|-------------|
| resolution_id | Many2one | Parent resolution |
| sequence | Integer | Print order of the signature blocks |
| partner_id | Many2one | Signatory |
| capacity | Selection | sole_shareholder / shareholder / sole_director / director / officer / proxy / other |
| capacity_custom | Char | Literal capacity; required when capacity is "other" |
| capacity_label | Char | Computed — what actually gets printed |
| purpose | Char | Printed under the capacity for a limited-purpose signature |

### corporate.director

| Field | Type | Description |
|-------|------|-------------|
| partner_id | Many2one | Director |
| appointment_date / end_date | Date | Term boundaries |
| end_reason | Selection | resignation / removal / term_expired / other |
| appointment_resolution_id / end_resolution_id | Many2one | Linked resolutions |
| domicile | Char | LSAQ Canadian-residency requirement |
| is_active | Boolean | Stored, computed from end_date |
| company_id | Many2one | Company |

### corporate.officer

| Field | Type | Description |
|-------|------|-------------|
| partner_id | Many2one | Officer |
| title | Selection | president / vice_president / secretary / treasurer / director_general / other |
| title_custom | Char | Custom title when title is "other" |
| appointment_date / end_date | Date | Term boundaries |
| appointment_resolution_id | Many2one | Linked appointment resolution |
| is_active | Boolean | Stored, computed from end_date |
| company_id | Many2one | Company |

### corporate.compliance.event

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Event name |
| event_type | Selection | annual_declaration / agm / director_election / auditor_appointment / financial_approval / bylaw_review / other |
| fiscal_year | Char | Fiscal year reference |
| due_date / completed_date | Date | Deadline, and when it was met |
| status | Selection | Stored, computed: upcoming / due_soon / overdue / completed |
| resolution_id | Many2one | Linked resolution |
| filing_reference | Char | REQ confirmation number or other filing reference |
| reminder_sent | Boolean | Set by the daily pass |
| company_id | Many2one | Company |

### project.document (extended)

| Field | Type | Description |
|-------|------|-------------|
| minute_book_section | Selection | charter / bylaws / agreements / director_minutes / shareholder_minutes / forms_filed / financial_statements |

## Security

| Group | Access |
|-------|--------|
| Corporate governance / Manager | Full access to resolutions, registers and compliance events |
| Documents / User (from the base module) | Read-only |

The manager group implies **Documents / Manager**: corporate documents are
documents, and a corporate manager who could not open the minute book would be
holding half a key.

Menus carry the group on the **parent** item only. A `menuitem`'s `groups`
attribute produces link commands, never unlink ones: adding a group there never
removes another, so a single carrier is one thing to change instead of six.

## Scheduled Actions

| Action | Frequency | What it does |
|--------|-----------|--------------|
| Corporatif : Vérifier les échéances de conformité | Daily | Creates an activity per corporate manager for events due within 30 days, then flags the event as reminded |

## Testing

```bash
odoo -d <db> -i bf_corporate_governance --test-enable --stop-after-init
```

Three files: the subsystem's own net (`test_corporate_governance.py`), the
module boundaries (`test_module_boundaries.py`), and the extraction invariants
(`test_extraction.py`) — which check that nothing corporate stayed behind under
the base module's name, that the migration's list mirrors what this module
declares, and that the resolution sequence kept counting.

## License

LGPL-3. See [LICENSE](LICENSE).
