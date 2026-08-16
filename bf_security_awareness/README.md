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
  (*Mon tableau de bord*: their own resilience and assigned courses).
- **Report a suspicious email.** Every internal user gets a *Rapporter un courriel
  suspect* button, and an optional employees-only mail alias lets them forward a
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
- **Clawback** — `clawback_mode` (`quarantine` default), `clawback_quarantine_folder`,
  `clawback_heuristic_window_days`, `clawback_require_preview`,
  `clawback_max_blast_messages` (blast-radius cap). Connector secrets are
  Fernet-encrypted with a key read from the environment / `odoo.conf`, never the DB.

## Deliverability (admin checklist)

Simulated lures must reach the inbox, so they need to bypass spam filtering on
purpose. Configure per tenant:

- **Sending profile** — set a dedicated *Serveur d'envoi* (`ir.mail_server`) on
  the campaign, ideally an authenticated SMTP sender you control.
- **SPF / DKIM** — authorize the sending host in the sending domain's SPF record
  and sign with DKIM, so the lures aren't auto-flagged.
- **Allow-list** — allow the sending IP / domain through the mail gateway and
  endpoint protection (and Microsoft 365 *Advanced Delivery* / spoof-intelligence
  policies for Defender) so sims land cleanly.
- **Send window** — use *Étalement des envois (jours)* to spread delivery across
  several days; this avoids a single blast and stops recipients warning each
  other.
- **Test first** — run a one-recipient campaign to yourself before targeting a
  group.

## Security

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
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.
