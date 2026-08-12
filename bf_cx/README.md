# Customer Experience (bf_cx)

A customer listening programme built into Odoo 18 Community: NPS, continuous
feedback, complaints and testimonials, with no external licence.

## Ecosystem

Sixteen `auto_install` bridge modules switch themselves on depending on what
else is installed:

| Bridge | Activates with | Brings |
|---|---|---|
| `bf_cx_helpdesk` | helpdesk_mgmt | Complaints team, tickets from a complaint or a detractor, optional automatic ticket |
| `bf_cx_privacy` | privacy_consent | Formal Law 25 consent for testimonials, propagated withdrawal, "Do not contact" list |
| `bf_cx_dashboard` | bf_dashboard | NPS tile, callbacks and open complaints on the dashboard |
| `bf_cx_meeting` | bf_meeting | 3-emoji feedback after a meeting report is sent (opt-in) |
| `bf_cx_digest` | daily_todo_digest | A "Customer experience" section in the daily digest |
| `bf_cx_crm` | crm | Post-loss survey plus NPS enrolment on won deals (opt-in) |
| `bf_cx_website` | website | Published testimonials rendered on `/temoignages` (instant Law 25 removal) |
| `bf_cx_appointment` | bf_appointment | 3-emoji feedback after an appointment (opt-in) |
| `bf_cx_hosting` | hosting_management | CSAT after a scheduled maintenance (opt-in) |
| `bf_cx_sign` | bf_sign | Micro-feedback after a signature (opt-in) |
| `bf_cx_sms` | bf_sms_archive | Survey invitations by SMS for contacts with no email (manual button) |
| `bf_cx_subscription` | bf_subscription | A "recurring revenue at risk" indicator on the tile |
| `bf_cx_onboarding` | bf_onboarding_base | The module's getting-started panel |
| `bf_cx_gamification` | bf_gamification | XP when a follow-up is completed and when a complaint is resolved |
| `bf_cx_mass_mailing` | mass_mailing | An option to exclude open CX loops from a mailing |
| `bf_cx_fundraising` | bf_fundraising_core | Post-donation donor experience survey (opt-in, for non-profits) |

A dedicated dashboard (date selector, NPS/satisfaction/complaints/response
rate, monthly trends, themes) ships as standard.

Client emails: branded shell (company logo, accent, Lexend), a signed
unsubscribe link in the footer, and bilingual FR/EN content (the `en_CA` slot).

## Solicitation guardrails

Every outbound request — waves, post-meeting, post-loss, AND the core's own
rating requests (project ratings through the central `rating_send_request`
hook, ticket closing CSAT) — goes through
`res.partner._bf_cx_split_solicitable()`:

- a per-contact **cooldown** (`bf_cx.solicitation_cooldown_days`, 30 days by
  default; overridable per program through `cooldown_days` — 90 days
  recommended for a relationship NPS) with a `bf_cx_last_solicited` stamp
  (reminders stamp it too);
- the **email blacklist** (`mail.blacklist`) — `mail.template` sends would
  otherwise bypass it;
- **active dunning** (the Blue Fox invoice follow-up module, detected at
  runtime through the `bf_followup_state` field): no "rate us" for a client on
  a second reminder or a formal demand;
- the **"Do not contact" list** from `privacy_consent` (through the bridge).

Deferred contacts are logged in the chatter and **picked up by the cron** once
the cooldown expires: a guardrail defers, it does not delete, otherwise it
biases the sample. Internal (360) programs are exempt, because the solicitation
budget is a client concept.

## Statistical honesty

The NPS on display (dashboard, digest, programs) uses a configurable rolling
window (`bf_cx.nps_window_days`, 365 days by default) and is **hidden below 10
responses** ("n too small"): below that the margin of error exceeds ±20 points.
The n is always shown next to the score.

## Complaints — ISO 10002

A real acknowledgement (email to the complainant + stored date + computed
delay), an automatic alert to the owner when the acknowledgement deadline
approaches, a root cause and corrective action required before resolution, and
**follow-up on the complainant's satisfaction after closure** (activity plus a
dedicated field).

## Pulse and QR

When a program's survey is set to public access, the program exposes the
permanent URL, an HTML snippet to paste into email signatures, and a
downloadable QR code (requires the `qrcode` Python library in the container —
imported lazily, so its absence never blocks installing the module).

## What the module does

### Unified feedback register
Every measured signal lands in `bf.cx.feedback`, whatever the channel:

- answers to program surveys (the `survey.user_input._mark_done` hook);
- email ratings from the core `rating` module (projects, tickets and so on),
  ingested on consumption;
- manual entries (meeting, phone).

Each entry carries the type (NPS, CSAT, comment, internal 360), the score, the
verbatim, the originating program/wave, the project and the follow-up owner.

### NPS programs and waves
A **program** ties an Odoo survey (a 0-10 "Scale" question) to a measurement
intent. **Waves** send the survey in batches: one individually tokenised answer
per contact (`_create_answer`), an invitation through a mail template, and an
automatic reminder to non-respondents (daily cron, per-program delay). The NPS
score (% promoters − % detractors) is computed per program and per wave.
Campaign attribution (`utm.campaign`) is carried by the wave, since the
survey ↔ campaign link does not exist in core.

### Closed loop
A detractor (NPS ≤ 6) or a dissatisfied rating (< 3/5) automatically creates a
follow-up activity assigned to the account owner (can be switched off in the
settings). Bridges can extend this behaviour (helpdesk ticket).

### Complaints
A standalone register: sequential number (PLT####), severity, acknowledgement
deadline (configurable delay), root cause analysis and corrective action. The
`bf_cx_helpdesk` bridge (auto-installed when `helpdesk_mgmt` is present) adds
linked ticket creation.

### Testimonials
Candidates are detected from surveys (an opt-in question), and publication is
blocked until consent is recorded (verbal, written, or formal through
`privacy_consent` with the `bf_cx_privacy` bridge). Withdrawal ("Pull") reminds
you where the testimonial is being used.

### Internal feedback (360)
"Internal" type programs use the same survey mechanics, but the entries are
about a person rather than a service, so three rules apply:

- a wave carries a **subject** (`subject_user_id`), and the resulting entries
  are filed under the person being **reviewed**, not the respondent. A subject
  is refused on a non-internal program, otherwise client answers would end up
  filed under an employee's name;
- `hide_respondent` (on by default) keeps the respondent out of the register,
  so lists, groupings and exports never name them. This masks the register, not
  the database: a manager still has technical access to the survey response
  itself, and in a small team a comment stays recognisable by its content. Say
  so before asking people to answer;
- a person's average **refuses to display below three responses**
  (`MIN_360_RESPONSES`), the same honesty rule as the n=10 NPS threshold,
  tightened for the smaller population a 360 draws from. Wave summaries are
  grouped per wave, so two rounds on the same person do not blend.

Entries remain restricted to the Manager group (record rule). A ready-to-use
"Rétroaction 360" program and survey ship with the module, along with a
dedicated invitation template that names the person being reviewed and carries
no unsubscribe link, since pointing an employee at `mail.blacklist` would block
them for client mail too.

## Architecture

- No hard dependency on the helpdesk or on the privacy module: the links live
  in the `auto_install` bridge modules (`bf_cx_helpdesk`, `bf_cx_privacy`).
- The NPS does NOT go through `rating.rating` (the core's 0-5 SQL constraint):
  it lives in the module's own register, fed from `value_scale`.
- Survey ingestion is idempotent (row lock plus per-answer deduplication), as
  `_mark_done` can be called again by core.

## Security

- `group_bf_cx_user` (Operator): manages programs, feedback, complaints and
  testimonials; does not see internal 360 entries.
- `group_bf_cx_manager` (Manager): everything, including the 360 and the
  configuration.
- Multi-company on all five models.
