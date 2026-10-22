# Security Awareness

A KnowBe4 / Terranova-style security-awareness platform for Odoo 18, developed
by [Les services de consultation Blue Fox, Inc.](https://symbifox.com)

It lets an organization run **simulated phishing campaigns**, keep a **per-person
risk profile**, deliver **cybersecurity eLearning**, let employees **report real
suspicious emails**, and **pull a confirmed malicious email out of every mailbox**
— all inside Odoo, reusing the native Email Marketing, Link Tracking and
eLearning (website_slides) engines.

## What it does

- **Phishing simulations.** A campaign sends a lure email (built from a reusable
  template) to a set of people. Each recipient gets a unique tracked link. The
  module records who opened, clicked, and — optionally — submitted credentials on
  a fake login page. Supports QR / quishing lures, attachments, A-of-many sending
  windows, and a per-campaign sending profile.
- **Teachable moment.** Anyone who clicks/submits lands on a branded page that
  clearly states it was an *authorized internal simulation*, explains the red
  flags, and links to assigned training.
- **Risk profiles.** Results aggregate per person into a tunable 0–100 risk score
  and a low/moderate/high/critical level, with recency decay and training
  mitigation.
- **Training remediation.** People who fail can be auto-enrolled into a
  cybersecurity course; completion is tracked and feeds back into the risk score.
- **Dashboards.** An OWL manager dashboard (org phish-prone % trend, repeat
  clickers, overdue training, recent campaigns) and an employee self-view
  (*My cybersecurity*: their own resilience and assigned courses).
- **Campaign reports.** The *Named report (confidential)* lists every person and
  their result; it stays inside the organization. The *Aggregate report* is the
  one a client can hand to a supplier or an insurer: lure, status, send date,
  number of recipients and a neutral reference (`SEN-0001`, numbered per
  company) always; open, click, submit and report rates only from
  `aggregate_min_recipients` recipients (10 by default, never below 5); the
  completed-training rate only from that many trainings assigned by the
  campaign itself. Click and submission rates of 90 % or more, and report and
  completed-training rates of 10 % or less, read as a range, and the change
  since the previous campaign (shown with that campaign's size) is left out for
  a bounded rate. It never prints a person, the campaign name (free text, which
  can carry a department), a risk profile or a time of action.
- **Report a suspicious email.** Every internal user gets a *Report a suspicious
  email* button, and an optional employees-only mail alias lets them forward a
  suspect message (as attachment) to create a report. If the report matches one of
  our own simulations the reporter is **credited** (no incident); anything else is
  triaged (optional Helpdesk ticket, a follow-up activity, and an ntfy alert).
- **Email clawback (PhishRIP).** Once a manager confirms a reported email is a
  real threat, a purge-privileged user can **search every internal mailbox and
  move the offending message to a reversible quarantine** — matched by exact
  `Message-ID` (from the forwarded `.eml`) or a reviewed From/Subject heuristic.
  See **`SECURITY.md`** for the full clawback security model.

## Credential capture is safe by design

The fake login page records only **that** a submission happened and, optionally,
the **length** of each field. There is deliberately **no database column** that
can hold a submitted username or password value. The controller only ever reads
`len(...)`; the raw values are never stored, logged, or written to the chatter.

## Privacy, ethics & roles

This tool is for **authorized internal security-awareness testing** of your own
organization. Behavioral results (who fell for a phish) are HR-private. Three
groups gate access:

- **Operator** — create/launch campaigns, view results.
- **Manager** — full back office, triage reported emails, *preview* a clawback.
- **Purge** — the privileged hat allowed to *execute / restore* a clawback
  (move mail in every mailbox). Separate from Manager on purpose.

Ordinary users see only their own self-service (their dashboard + the report
button); they cannot read campaigns, other people's scores, the triage queue, or
the clawback. Every landing page declares the simulation and states that no
credentials were stored.

## How it works (architecture)

The module is a thin domain layer over native Odoo:

- `bf.phishing.result` is both the per-recipient tracking record **and** the
  target model of a native `mailing.mailing`. The lure body uses QWeb
  (`object.landing_url`) so each recipient gets a unique `/phish/<token>` link.
- Opens/clicks/submits are captured in real time by the `/phish` controller;
  `mailing.trace` is reconciled for delivery/bounce.
- Training uses `slide.channel` / `slide.channel.partner` (website_slides).
- Training follow-up (since 18.0.2.1.0): *Send reminder* sends the invitation in
  the company's mail layout, with the button colour, date and signature of the
  training register's reminder, and leaves **one** open to-do for whoever
  assigned the training. The daily overdue job adds one only when none is open,
  and does not email the assignee. An assignment from the generic register (no
  awareness course) gets the register's own reminder instead. The 18.0.2.1.0
  upgrade recreates the invitation template: local edits to it are replaced.
- Reporting (`bf.reported.phish`) is a `mail.thread` alias target; clawback uses
  a `bf.mail.clawback.connector` (Microsoft 365 app-only XOAUTH2, or per-mailbox
  app password) driving `imaplib` — no extra Python dependency.

## eLearning note

Course completion is only recorded for a **logged-in** portal/internal user.
When a person without an account is assigned training, the module (if
`bf_security_awareness.auto_grant_portal` is on, the default) creates a portal
user so the eLearning sign-in/invite flow can give them access; otherwise it
enrolls them as *invited* and the dashboard shows completion can't be recorded
until they sign in.

## Configuration

All tunable without code, as `ir.config_parameter` keys under the
`bf_security_awareness.` namespace:

- **Scoring** — `weight_submitted`, `weight_clicked`, `weight_opened`,
  `weight_reported`, `risk_window_days`, `training_mitigation`,
  `difficulty_weight_{easy,medium,hard}`, `training_due_days`, `auto_grant_portal`.
- **Reporting / triage** — `report_ntfy_url`, `report_ntfy_token`,
  `report_ntfy_priority`, `report_responsible_user_id`, `report_helpdesk_team_id`,
  `sim_match_window_days` (secrets are never seeded in code; set per tenant).
- **Aggregate report** — `aggregate_min_recipients` (10; a value under 5 is
  raised to 5).
- **Clawback** — `clawback_mode` (`quarantine` default), `clawback_quarantine_folder`,
  `clawback_heuristic_window_days`, `clawback_require_preview`,
  `clawback_max_blast_messages` (blast-radius cap). Connector secrets are
  Fernet-encrypted with a key read from the environment / `odoo.conf`, never the DB.

## Deliverability (admin checklist)

Simulated lures must reach the inbox, so they need to bypass spam filtering on
purpose. Configure per tenant:

- **Sending profile** — set a dedicated *Outgoing server* (`ir.mail_server`) on
  the campaign, ideally an authenticated SMTP sender you control.
- **SPF / DKIM** — authorize the sending host in the sending domain's SPF record
  and sign with DKIM, so the lures aren't auto-flagged.
- **Allow-list** — allow the sending IP / domain through the mail gateway and
  endpoint protection (and Microsoft 365 *Advanced Delivery* / spoof-intelligence
  policies for Defender) so sims land cleanly.
- **Send window** — use *Send spreading (days)* to spread delivery across
  several days; this avoids a single blast and stops recipients warning each
  other.
- **Test first** — run a one-recipient campaign to yourself before targeting a
  group.

## Languages (18.0.2.2.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. Each text is written in the language of whoever will read it, not of whatever triggers it (the public landing page, the mail gateway, a scheduled job). Stored names, notes, the Helpdesk ticket and ntfy alerts are read by the security team: they follow the company's language. The triage activity and the training reminder follow the assignee, else the company. Enrollment notes and a failed mass mailing follow the operator who acts, else the company. The training invitation follows the invited person, else their organization, else the company. The dashboard and error messages follow the user who sees them, and the public pages the visitor. On upgrade, the shipped scheduled jobs, training invitation template and training category switch to English only where the database still holds the text that was shipped; an edited value is left alone.

## Security

**Link scanners are not people.** Email security gateways, antivirus, link
previews and headless browsers open every link of a message before its
recipient. Their visits (recognised by their user agent, and every `HEAD`
request) are recorded neither as clicks nor as reports, so campaign results
reflect people. It is a filter, not a proof: a scanner that imitates a real
browser still gets through.

**Odoo does not visit its own lures** (since 18.0.2.0.4). When a campaign is
sent, Email Marketing shortens every link and Link Tracking fetches each page to
read its title, which made the server itself open every `/phish/<token>` link
(a possible false click, assigned training and false report per recipient).
Simulation links of this database now get a fixed title with no network
request, and any request carrying Odoo's `Odoo-Link-Preview` header is treated
as a scanner: no click, open, submission or report is recorded.

**Lure bodies are sanitized on save** (Odoo's outgoing-email mode): the tracked
link and the formatting survive, scripts do not. Lures written before 18.0.2.0.3
are sanitized once at upgrade, in every language, and each changed record is
logged. Landing pages are not touched.

**A report only takes its own uploads** (since 18.0.2.1.0): a report keeps only
the files the reporting employee uploaded in its own form. Any other attachment
is refused before anything is created, so a report cannot pull a file out of
another record.

The clawback and the reporting alias are high-impact. Read **`SECURITY.md`** for
the threat model and controls (least-privilege groups, reversible quarantine,
IMAP-injection screening, the authorization flag, blast-radius cap, encrypted
secrets, and the operator-side Entra / DMARC hardening expected at deployment).

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-10-22, this version converts automatically to
  **LGPL-3.0-or-later**.
