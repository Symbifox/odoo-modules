# Confidentiality-incident register (`privacy_incident`)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: LGPL-3](https://img.shields.io/badge/License-LGPL--3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)

An Odoo 18 CE module covering the full life of a confidentiality incident under Quebec's
**Law 25**: declaration, risk assessment, notices, measures. It keeps the register the law
requires.

---

## The two texts behind this module

| Text | What it requires |
|---|---|
| **CQLR c. P-39.1**, ss. 3.5 to 3.8 | Definition of an incident (s. 3.6), reasonable measures, notice to the Commission d'accès à l'information and to the people concerned when there is a risk of serious injury, the register (s. 3.8) |
| **CQLR c. A-2.1, r. 3.1**, Regulation respecting confidentiality incidents | Content of the notice to the Commission (s. 3), of the notice to the person concerned (s. 5), of the register (s. 7), retention (s. 8) |

The regulation is filed under chapter A-2.1, even for private-sector organisations.

## The eight register items (s. 7) and their fields

| s. 7 | Prescribed item | Fields |
|---|---|---|
| 1° | Information concerned, or why it cannot be described | `pi_description`, `pi_description_unknown`, `pi_unknown_reason` |
| 2° | Brief description of the circumstances | `circumstances` |
| 3° | Date or period of occurrence, approximation accepted | `occurrence_date`, `occurrence_date_end`, `occurrence_is_approximate`, `occurrence_date_note` |
| 4° | Date or period of awareness | `awareness_date`, `awareness_date_end` |
| 5° | Number of people concerned, approximation accepted | `subject_count`, `subject_count_is_estimate` |
| 6° | What leads to conclude that there is, **or is not**, a risk of serious injury | `sensitivity_analysis`, `malicious_use_analysis`, `consequences_analysis`, `misuse_analysis`, `serious_harm_risk`, `risk_rationale` |
| 7° | If the risk is serious: dates of the notices, public notice and its reason | `cai_notification_date`, `subjects_notification_date`, `public_notice_given`, … |
| 8° | Brief description of the measures taken | `measure_ids` (`privacy.incident.measure`) |

`register_gaps` lists in plain words what is still missing, with the item number;
`register_complete` is true once all eight are covered.

## Three traps in the text

1. **The "or is not" of item 6° widens the register**: every incident is recorded,
   including those concluded to carry no risk of serious injury. `serious_harm_risk` is a
   conclusion documented both ways, never an entry filter.
2. **Occurrence and awareness are two distinct dates**, each of which may be a period.
   The five-year retention (s. 8) runs from **awareness**: `register_retention_until`.
3. **No numbered deadline exists.** The law says "promptly". `days_since_awareness` is a
   follow-up indicator and says so; it is not a regulatory countdown.

## A provider's notice

A service provider that holds your information (hosting provider, outsourcer) must notify
you without delay of any breach or attempted breach (s. 18.3). Its notice carries facts;
this is where they become an incident, or not.

The *Provider's notice* tab keeps the provenance: the provider, the reference and version
of its notice, the time of receipt (in principle your awareness date, s. 7, 4°), the PDF
received and its **SHA-256 fingerprint, computed from the bytes** and never copied over.
A notice received through federation is locked: its provenance cannot be edited, its PDF
can only be replaced by a higher version, and the stored PDF is guarded against direct
changes. The integrity check reads the bytes, also when the web client loads the form.

With `bf_federation_privacy`, a paired provider's notice creates the record by itself, and
its updates follow it without touching what you wrote.

## Client portal

- `/my/incidents`: the organisation's register
- `/my/incidents/<id>`: one entry, read-only
- `/my/incidents/declarer`: guided declaration, seven fields

No entry is added to `/my`: opening the portal to clients is the instance's decision.

## Isolation

- A record rule for `base.group_portal`, anchored on the user's commercial partner: an
  incident without an organisation is visible to no client.
- Internal content never reaches the portal: `internal_notes` is reserved to internal
  users (not even readable by RPC from the portal), and the thread is in no portal template.
- The portal cannot create on the model: the declaration goes through the controller with
  a whitelist of seven fields; the organisation and the provenance are set server-side.
- The followers of a register record are visible to the privacy role only, within the
  record's company, and notifications only reach the people named on a message and the
  record's followers.

## Not done yet

- The notices to the Commission (s. 3) and to the person concerned (s. 5) are not
  generated as documents; their fields are there.
- No register export beyond Odoo's standard export.

## Dependencies

`privacy_consent`.

## Licence

LGPL-3, see `LICENSE`.
