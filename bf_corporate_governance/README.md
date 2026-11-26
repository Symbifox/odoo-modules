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

Built for Quebec organisations: business corporations under the LSAQ (*Loi sur
les sociétés par actions*), non-profit legal persons under Part III of the
*Loi sur les compagnies*, and cooperatives. Only the shipped compliance
templates are province-specific; the registers and resolutions are not.

- **Resolutions**: board and shareholder, with a lifecycle (Draft → Proposed →
  Adopted / Rejected / Superseded) and a branded PDF ready to sign. Members'
  resolutions, adopted at the members' meeting of a non-profit or an
  association, are kept the same way.
- **Signature blocks** that say who signs *and in what capacity*, rather than
  guessing it from a register
- **Director register** with appointment and end dates, end reason, linked
  resolutions, and domicile for the LSAQ Canadian-residency requirement
- **Officer register** with appointment history
- **Compliance calendar** generated per fiscal year and per company from
  sourced templates, with a daily reminder pass
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
| Members' resolution | Adopted at a members' meeting of a non-profit or an association. The PDF header reads *Résolution de l'assemblée des membres*, and two signatory capacities fit it: meeting chair (*Présidence d'assemblée*) and meeting secretary (*Secrétariat d'assemblée*) |

Subject categories: officer appointment, director election, dividend
declaration, share issuance, bylaw amendment, contract approval, bank
authorization, auditor appointment, fiscal year, financial approval,
dissolution, other.

A reference is drawn from the `corporate.resolution` sequence (`RES-YYYY-NNN`)
on creation. Votes for / against / abstaining are recorded, along with an
"adopted unanimously" flag, the mover and the seconder.

### Who signs, and as what

The printed resolution reads its signature block in four steps, in order:

1. **Signatory lines**, when any are entered — they win, with the capacity
   typed on each line;
2. otherwise, for a **board** resolution — the directors **in office on the
   date of the meeting**, signing as directors;
3. otherwise, for a **members'** resolution: two lines to fill in, under
   « Présidence d'assemblée » and « Secrétariat d'assemblée ». A members'
   resolution is signed by the people who held the assembly, not by those who
   moved or seconded;
4. otherwise, the mover and the seconder, **named without a capacity**.

On a members' resolution, a signatory line has **no default capacity**: it must
be chosen (usually the assembly chair or secretary), in the form as through an
RPC call or an import. Elsewhere the default stays « Actionnaire ». The
shareholder capacities (« Actionnaire », « Actionnaire unique ») are refused on
a members' resolution, whether a line is created there, moved to it, or the
resolution's type changes to members. « Autre » remains free text: what is
typed there is not checked.

Step 4 is deliberate. The module keeps no shareholder register: it knows who
carried the resolution, not the capacity in which that person signed it.
Printing a guess under a name is worse than printing a name.

Step 2 reads *at the date of the meeting*, not today. A resolution is often
printed months or years after the fact, and "who is a director now" would name
people who had not yet been elected.

### Compliance calendar

The module ships **templates**, never dated events. A template carries one
recurring obligation: its legal form, its area (corporate law, tax, health and
safety), its legal basis with a link to the text in force, and a **deadline
rule** — so many months after the fiscal year ends, or a fixed date the year
after it, with an optional second bound (*within 15 months of the previous
meeting*) and a first-year bound counted from incorporation. The nearer bound
wins.

**Generate a fiscal year** (button on the calendar, or its own menu) asks for the
company, its legal form and the fiscal year end, and offers one line per
applicable template with the deadline the rule gives. Obligations that only
apply in some cases (employees, dividends, registered charity…) are offered
unticked. Untick what does not apply, correct a date if needed, and generate.
An obligation already generated for that company and fiscal year is shown
greyed out and never created twice; the database refuses a duplicate as well.
The legal form, incorporation date and fiscal year end are kept on the company
(*Corporate profile* tab) to prefill the next year.

A generated event keeps its own copy of the template's name, notes and legal
basis. An upgrade that corrects a template therefore never touches events
already generated. Shipped templates are rewritten at every upgrade: to adapt
one, duplicate it and archive the original.

A daily scheduled action refreshes each pending event's status (it is stored, and
used to keep the value of its last write), then creates an activity for every
corporate manager when an event falls due within 30 days, with a priority that
rises as the date approaches.

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

## Languages (18.0.1.2.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. A resolution's PDF prints its header title, the "Director" capacity under each name when it falls back on the board, and each signatory's capacity in the language it is rendered in. The daily compliance check writes each corporate manager's activity in that manager's language, not the job's, and creates it in their environment, so Odoo's "assigned to you" notice follows them too. The dashboard's window title is extracted and receives its French. On upgrade, the scheduled job's name switches to English only where the database still holds the name that was shipped.

## Data Model

### corporate.resolution

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Resolution title |
| sequence | Char | Auto-generated reference (RES-YYYY-NNN) |
| resolution_type | Selection | board / shareholder / written_board / written_shareholder / members |
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
| capacity | Selection | sole_shareholder / shareholder / sole_director / director / officer / proxy / assembly_chair / assembly_secretary / other |
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
| event_type | Selection | annual_declaration / agm / director_election / auditor_appointment / financial_approval / bylaw_review / annual_report / tax_return / tax_payment / information_return / ohs_filing / ohs_program / other |
| template_id | Many2one | Template the event was generated from (empty when entered by hand) |
| fiscal_year | Char | Fiscal year label |
| fiscal_year_end | Date | Last day of the fiscal year the event belongs to |
| legal_basis | Char | Copied from the template at generation |
| due_date / completed_date | Date | Deadline, and when it was met |
| status | Selection | Stored, computed: upcoming / due_soon / overdue / completed |
| resolution_id | Many2one | Linked resolution |
| filing_reference | Char | REQ confirmation number or other filing reference |
| reminder_sent | Boolean | Set by the daily pass |
| company_id | Many2one | Company |

Unique: one event per template, company and fiscal year end.

### corporate.compliance.template

| Field | Type | Description |
|-------|------|-------------|
| name, code | Char | Obligation, and its stable code |
| legal_form | Selection | qc_business_corporation / qc_nonprofit / qc_cooperative / any |
| scope | Selection | corporate / tax / ohs |
| event_type | Selection | As on the event |
| legal_basis, reference_url, notes | Char, Char, Text | Where the obligation comes from |
| condition | Char | When it applies; a conditional template is offered unticked |
| anchor | Selection | fiscal_year_end / fixed_date |
| offset_months, offset_days | Integer | After the fiscal year end |
| fixed_month, fixed_day | Selection, Integer | Fixed date the year after the fiscal year ends (day 31 = last day of the month) |
| cap_months_after_previous | Integer | Second bound, after the previous occurrence |
| first_cap_months_after_incorporation | Integer | Bound for the first occurrence |

### res.company (extended)

| Field | Type | Description |
|-------|------|-------------|
| corporate_legal_form | Selection | Legal form |
| corporate_incorporation_date | Date | Date of incorporation |
| corporate_fy_end_month, corporate_fy_end_day | Selection, Integer | Fiscal year end that drives the legal deadlines — kept apart from the accounting one on purpose |

### project.document (extended)

| Field | Type | Description |
|-------|------|-------------|
| minute_book_section | Selection | charter / bylaws / agreements / director_minutes / shareholder_minutes / forms_filed / financial_statements |

## Security

| Group | Access |
|-------|--------|
| Corporate governance / Manager | Full access to resolutions, registers, compliance events and templates; the generator |
| Documents / User (from the base module) | Read-only |

Two record rules: a compliance event is visible to the companies it belongs to
only, and a generator form only to the person who opened it (it is a transient
model, so a real table).

The manager group implies **Documents / Manager**: corporate documents are
documents, and a corporate manager who could not open the minute book would be
holding half a key.

Menus carry the group on the **parent** item only. A `menuitem`'s `groups`
attribute produces link commands, never unlink ones: adding a group there never
removes another, so a single carrier is one thing to change instead of six.

## Scheduled Actions

| Action | Frequency | What it does |
|--------|-----------|--------------|
| Corporate: check compliance due dates | Daily | Refreshes the status of pending events, then creates an activity per corporate manager whose companies include the event's, for events due within 30 days, and flags the event as reminded |

## Testing

```bash
odoo -d <db> -i bf_corporate_governance --test-enable --stop-after-init
```

Three files: the subsystem's own net (`test_corporate_governance.py`), the
module boundaries (`test_module_boundaries.py`), and the extraction invariants
(`test_extraction.py`) — which check that nothing corporate stayed behind under
the base module's name, that the migration's list mirrors what this module
declares, and that the resolution sequence kept counting. A fourth,
`test_compliance_templates.py`, holds the compliance calendar: no dated event
shipped, the deadline rule on its edges (month ends, leap February, two bounds),
the generator never creating twice, the removal of the old seeded events, and
the record rules.

## Changelog

- **18.0.2.0.1**: the generator offers the obligations of the chosen legal form
  first, then those of every form, and its list shows each obligation's full
  name (wider window, technical column hidden).

- **18.0.2.0.0**: templates instead of dated events. Until 18.0.1.2.0 the module
  created five compliance events dated 2026-06-30 — the publisher's own — in
  every database it was installed in, where they showed as overdue obligations
  that were nobody else's. They are removed at upgrade when untouched (delivered
  name, notes, year and deadline; no completion, resolution, filing reference,
  attachment, message, tracked change, closed reminder or hand-planned
  activity); an event someone touched is kept, detached from the module and
  tied to the matching template. The removal runs at every install and upgrade,
  so it also catches the copies a late `project_knowledge_matrix` upgrade hands
  over. New: 33 compliance templates for Québec business corporations,
  non-profits and cooperatives plus the tax and health-and-safety areas, each
  with its legal basis and a link to the text in force; a per-year generator; a
  corporate profile on the company; a company record rule on events; reminders
  sent only to managers who can open the event; and a daily status refresh (the
  stored status used to keep the value of its last write).

- **18.0.1.2.0**: labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. The resolution PDF prints its titles and capacities in the language it is rendered in, and each corporate manager receives the compliance reminder in their own language. See the Languages section.
- **18.0.1.1.1**: the resolution PDF takes its palette from the resolution's
  company, like its logo and name, rather than from the first company of the
  user's session.
- **18.0.1.1.0**: members' resolutions. A new resolution type for resolutions
  adopted at a members' meeting of a non-profit or an association, with its
  PDF header (« Résolution de l'assemblée des membres ») and signature heading
  (« Signatures de l'assemblée »); two signatory capacities, assembly chair and
  assembly secretary; without signatory lines, a members' resolution prints
  two lines to fill in under those capacities; on a members' resolution, a
  signatory's capacity has no default, must be chosen, and cannot be a
  shareholder capacity; a « Membres » search filter.
- **18.0.1.0.0**: first release, extracted from `project_knowledge_matrix`
  18.0.12.0.0.

## License

LGPL-3. See [LICENSE](LICENSE).
