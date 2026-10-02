# Membership: general meetings and corporate register (`bf_membership_assembly_governance`)

A proposal adopted by the members' meeting is a resolution. It belongs in the
minute book as much as in the meeting's minutes, and copying it by hand means
one day copying it with a wrong number.

This module is the bridge between `bf_membership_assembly` (members' meetings)
and `bf_corporate_governance` (the register of resolutions). It installs
itself automatically when both are present and adds no model of its own.

The interface is in French; the labels below are quoted as they appear on
screen.

## What it does

On an **adopted** proposal, once the assembly is **closed**, the button
« Inscrire au registre corporatif » (record in the corporate register) creates
the resolution (`corporate.resolution`) with what the assembly decided:

| Register field | What it receives |
|---|---|
| `name`, `resolved_text` | The proposal's title and text |
| `resolution_type` | « Résolution des membres » (members' resolution) |
| `signatory_ids` | The assembly's chair and secretary, as « Présidence d'assemblée » and « Secrétariat d'assemblée », when the assembly names them. They are frozen at closing, and the recorded resolution's signatory lines cannot be added, changed or removed in the register outside superuser |
| `meeting_type` | Annual general meeting if the assembly is annual, special meeting otherwise |
| `meeting_date` | The day of the assembly, in the organisation's time zone |
| `effective_date` | Set by the register's adoption to the meeting date, when empty |
| `vote_for`, `vote_against`, `vote_abstain` | The vote totals |
| `unanimously_adopted` | True only with votes for and no vote against, no abstention and no spoiled ballot |
| `status` | Adopted, through the register's own `action_adopt()` |
| `notes` | The assembly, its date, the voting method and the required majority. The whole note is written in the organisation's language (the company's), and its date is spelled out in that language's format, not in the format of the interface of the person registering |

On a closed assembly, « Inscrire les résolutions adoptées » does the same for
all its adopted proposals at once. Each entry is logged on the assembly with
the resolution's reference, and the resolution form shows the assembly and the
proposal it came from. A corporate governance manager who has no Members role
can open the resolution and see which assembly it came from.

The bridge does **not** copy who moved and who seconded into the register:
the corporate register is read by people who have no Members role, and those
names say which members carried the proposal. They stay on the proposal and in
the assembly's minutes.

A resolution recorded by the bridge **keeps what the assembly adopted**: its
title, text, totals, type and meeting cannot be rewritten in the register
outside superuser, and it cannot be deleted there (deleted, it would free its
proposal, which would be recorded again): a replaced resolution is marked
superseded, like any other. Its signatories are frozen likewise (no line
added, changed or removed), and its status no longer changes except to mark
it superseded: the assembly adopted it. The rest of the record (effective
date, preamble, notes, documents) follows the register's own life.

## Four decisions, and why

### 1. The link is written on the register side only

The resolution carries `bf_assembly_proposal_id`; the proposal reads its
resolution through the inverse field. A proposal is locked as soon as its
assembly closes, and the resolution is recorded after closing: writing the
link on the proposal would mean breaking that lock. A single written field is
also a link that cannot contradict itself.

That link is written **only by the bridge**, with superuser rights: it is
refused when a resolution is created (in the values or through a `default_*`
context key) and on any later change outside superuser. Otherwise a register
manager, even without a Members role, could link a resolution of their own to
a proposal; the bridge would then believe the proposal already recorded and
refuse to record the adopted resolution. The bridge creates the resolution in
the person's own role, with the register's rights, then sets the link with
superuser rights.

### 2. No duplicate, even with two simultaneous clicks

A second click opens the existing resolution instead of creating one. A
Python check is not enough against two simultaneous clicks, which would both
pass it: a unique constraint on `bf_assembly_proposal_id` in the database is
the safety net.

### 3. Nothing before closing, nothing rejected

While the assembly is open the totals can still change; a resolution recorded
at that point would no longer say what the assembly decided. A rejected
proposal never becomes a resolution. Adoption is **re-evaluated** from the
proposal's totals and required majority at the moment of recording, rather
than read from the stored result field. An assembly that did not reach its
quorum decides nothing: its "adopted" proposals are not recorded, and the
quorum is re-evaluated from the attendance on the list, like adoption from the
totals. `bf_membership_assembly` also guarantees that a proposal never moves
to another assembly, that what was put to the vote is frozen at the first
count, and that the chair and secretary are frozen at closing; since the
recorded resolution keeps those values in the register too, its meeting,
text, totals and signatories remain those of the assembly that actually
voted.

### 4. The register's access rights are not bypassed

Only a corporate governance manager (`bf_corporate_governance` « Gestionnaire »
group) records a resolution; anyone else is told so in plain words before
anything is written, and the buttons are shown to that group only. A
membership agent still sees, on the proposal, which resolution it became: that
field is read with superuser rights and opens nothing else in the register.

## « Résolution des membres »

The corporate register used to know only board and shareholder resolutions,
so a non-profit's resolution was recorded as a shareholders' resolution, and
its PDF header said so. `bf_corporate_governance` 18.0.1.1.0 adds the members'
resolution type and the assembly chair and secretary signatory capacities: the
header reads « Résolution de l'assemblée des membres », and the signatories
are the assembly's chair and secretary.

## Requirements

Odoo 18 Community. `depends`: `bf_membership_assembly`,
`bf_corporate_governance` (which itself depends on `project_knowledge_matrix`).
`bf_corporate_governance` must be at version **18.0.1.1.0** or later: earlier
versions have neither the members' resolution type nor the chair and
secretary capacities. No external Python dependency.

## Installation

Nothing to configure. With `bf_membership_assembly` and
`bf_corporate_governance` installed, this module installs automatically
(`auto_install`). Give the people who record resolutions the corporate
governance manager group, and name the chair and secretary on each assembly
before closing it.

## Tests

The tests cover the resolution created once and linked both ways, the
assembly officers as signatories, the second click that creates nothing, the
database constraint against a duplicate, the special assembly as a special
meeting, the refusal before closing and for a rejected proposal, adoption read
from the totals rather than the stored result, the register's rights played in
the role of a membership agent, a register manager without a Members role
reading the resolution, the link refused to that person outside the bridge
(at creation, through a context default, and on a later change), the note
dated in the organisation's language when the person registering works in
another language, the refusal without quorum, the recorded resolution that
can be neither rewritten nor deleted in the register, its signatories and
status frozen, and the mover and seconder that are not copied there.

```bash
odoo -d <database> -u bf_membership_assembly_governance --test-enable \
     --test-tags /bf_membership_assembly_governance --stop-after-init
```

## Known limitations

* **The register does not receive who moved or who seconded**: those names
  stay with the assembly, readable by the Members role.
* **The superuser keeps control**: the checks (link, content, signatories,
  status, deletion of a recorded resolution) apply outside superuser only.
* **The resolution's note is written in the organisation's language**; its
  source text is French, and uses its translation when one exists.
* **`bf_corporate_governance` 18.0.1.1.0 or later is required**: earlier
  versions have neither the members' resolution type nor the assembly chair
  and secretary capacities.

## Changelog

- **18.0.1.0.2**: first public release.

## License

Business Source License 1.1. The manifest says `Other proprietary` because the
manifest schema has no BUSL value; the `LICENSE` file governs.

* Licensor: Les services de consultation Blue Fox, Inc.
* You may use the module in production for your own internal business
  operations. Providing it to third parties as a product or service (hosted,
  managed or resold) requires a separate written agreement with the Licensor.
* Each version converts to LGPL-3.0-or-later at its Change Date; for this
  version, `LICENSE` sets 2030-10-01.

See `LICENSE` for the full terms.
