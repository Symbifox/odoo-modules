# bf_security_awareness — Roadmap

Status of the module today and the planned evolution toward KnowBe4 / Terranova
feature parity. Everything below is **deferred** (not yet built); the current
release (v18.0.1.2.1) covers the core engine, per-person risk profiles, the
website_slides training loop, and the simulated credential-capture landing flow.

Effort key: **S** ≈ ≤1 day · **M** ≈ 2–4 days · **L** ≈ 1–2 weeks.
"Reuse" names the native Odoo machinery to build on rather than reinvent.

---

## Parity snapshot (vs. KnowBe4 / Terranova / Hoxhunt / Proofpoint / GoPhish)

| Area | Status |
|---|---|
| Phishing engine (tokenized lures, open/click/submit, mass-mailing send) | 🟢 Have |
| Landing pages (credential / awareness / link-only + teachable moment) | 🟢 Have |
| Training / LMS (website_slides course, quiz, auto-enrol on failure) | 🟢 Have |
| Per-person risk score & level | 🟢 Have |
| Reporting dashboard, org Phish-prone % **trend** | 🟢 Have (v1.3) |
| Recurring programs / smart targeting | 🔴 Missing |
| Reported-phishing button + triage (sim-aware, Helpdesk + ntfy) | 🟢 Have (v1.4) |
| Email clawback / remediation (PhishRIP: pull a real malicious email from every internal mailbox, reversible) | 🟢 Have (v1.6) |
| Deliverability admin (sending profile, allow-list, throttle) | 🔴 Missing |
| Retention / anonymization (Loi 25) | 🔴 Missing |
| Smishing / QR / attachments / A/B | 🟡 Partial-none |
| Integrations (AD/SCIM, SIEM/webhook) | 🔴 Missing |

---

## v1.1 — Hardening & quick wins ✅ SHIPPED (v18.0.1.2.0)

Bug fixes: race-safe training auto-assign, marker-based open-pixel, visible
enrolment-`error` state, sub-day recency precision, `last_failed_date`
bool/datetime fix.

### v18.0.1.2.1 patch

- **Mailing-list audience fix.** `_resolve_target_partners` resolved list
  contacts via `mailing.contact.partner_id`, which doesn't exist in stock Odoo —
  preparing a campaign targeted by *Listes de diffusion* raised
  `KeyError: 'partner_id'`. Now each list contact is resolved to a `res.partner`
  by email (find-or-create). Covered by `test_audience_from_mailing_list`.
- Removed a dead frontend SCSS asset (the landing pages are self-contained,
  inline-styled, and never load `web.assets_frontend`).

Quick-win features delivered:
- ✅ **Sending profile per campaign** — `mail_server_id` on the campaign, passed
  to the mailing.
- ✅ **Send-window spread / throttle** — `send_window_days` jitters each result's
  `scheduled_send`; `_release_due_results()` drips batches via the 30-min cron.
- ✅ **QR / quishing lures** — `template.qr_code` injects a QR (`result.qr_src`
  via Odoo's public `/report/barcode` controller, encoding the landing URL).
- ✅ **Attachment lures** — `template.attachment_ids` flow to the mailing.
- ✅ **Campaign PDF report** — QWeb report (generic `web.external_layout`, no
  brand dependency): funnel + per-person detail. CSV export already free.
- ✅ **Deliverability checklist** — admin guide in README (sending profile, SPF/
  DKIM, allow-list, M365 Advanced Delivery, send window).

Still deferred from v1.1:
- **Department slicing** — needs an `hr` bridge (`hr.employee.department_id`),
  which the module deliberately doesn't hard-depend on. Will land with the
  optional HR integration in v1.3 (manager escalation).

## v1.2 — The visible value (M–L)

- ✅ **Manager dashboard + employee self-view** SHIPPED (v18.0.1.3.0). OWL
  client actions `bf_secaware_dashboard` (manager/operator, group-guarded) and
  `bf_secaware_my` (any internal user, sudo-scoped to own partner). Headline
  KPIs (phish-prone %, report rate, high-risk, overdue training), an SVG trend
  derived live from campaign history (no snapshot table yet), risk distribution,
  and actionable lists (repeat clickers, overdue training, recent campaigns).
- **Risk-trend snapshots** — M. Still deferred: the dashboard trend is derived
  from campaign months. A daily `bf.security.snapshot` (date, company, dept,
  phish_prone_pct, avg_risk, counts) would capture org risk *decay* between
  campaigns and enable dept slicing. Add when dept/HR bridge lands.
- **Recurring "programs"** — L. New `bf.security.program` (cadence, template pool,
  audience, difficulty ramp) + cron that spawns and launches a campaign each
  cycle. Reuse: existing campaign engine + `ir.cron`. The "always-on" model.
- **Smart / dynamic audiences** — M. `bf.security.audience` holding a stored Odoo
  `domain` (e.g. `risk_level in (high,critical)`, or "clicked last campaign"),
  resolved at prepare-time → enables **repeat-clicker auto-targeting**.
- **A/B template testing** — M. Campaign holds `template_ids` (M2m); assign each
  result a template at random on prepare (the body QWeb already renders
  per-record via `object.template_id`); compare click rate per variant.
- **Standalone & recurring training campaigns** — M. Generalize
  `bf.training.assignment` creation beyond "on failure": a `bf.training.campaign`
  enrols a whole audience and nags via the existing reminder cron.
- **Certificates of completion** — S–M. Branded QWeb PDF certificate on
  completion (reuse `bluefox_branding`), or website_slides certification via
  `survey`. Useful as Loi 25 / compliance evidence.

## v1.4 — Phish Alert Button & triage ✅ SHIPPED (v18.0.1.4.0)

- ✅ **"Rapporter comme malveillant" report button** for every internal user
  (`bf.report.phish.wizard` popup: best-practice category dropdown, sender,
  subject, suspicious link, free-text, optional attachments). Reachable from the
  *Ma cybersécurité* self-view and its menu. No `bf_email` dependency — works for
  any mailbox (plain IMAP, Discuss, etc.).
- ✅ **Sim-aware branching** (`bf.reported.phish.process`): if the report matches
  one of our simulations (precise `/phish/<token>` link, or recent same-subject
  campaign to the reporter), the reporter is **credited** (`register_report`,
  state *simulation*) and **no incident** is raised. Any other email is triaged.
- ✅ **Triage sequence** for real reports: optional **Helpdesk ticket**
  (`helpdesk.ticket`, soft — only if installed), a follow-up **activity** to the
  security lead, and an **ntfy alert** (config-driven URL/token, defaults off).
  Manager triage queue with *confirm threat / false alarm* states.

## v1.6 — Email clawback / PhishRIP ✅ SHIPPED (v18.0.1.6.0)

- ✅ **Mailbox connector** (`bf.mail.clawback.connector`) with two backends so
  "internal" scope works on any stack: `m365_oauth` (app-only OAuth2 +
  XOAUTH2 IMAP, one Entra app reaches every mailbox granted `FullAccess`) and
  `imap_password` (per-mailbox app password). Secrets Fernet-encrypted, key out
  of the DB.
- ✅ **Clawback operation** (`bf.clawback.operation` + per-mailbox
  `bf.clawback.hit`): from a confirmed-threat report, **Aperçu** (dry-run,
  mandatory before a heuristic run), **Exécuter** (move matches to a
  reversible quarantine folder), **Restaurer**. Token minted once per sweep,
  commit per mailbox → resumable by `cron_process_clawback`.
- ✅ **Matching**: exact `Message-ID` parsed from an attached `.eml`, else a
  From + Subject + date-window heuristic the manager reviews first.
- ✅ Manager-only; full chatter audit; ntfy alert on execute; **no message body
  is ever stored**.
- ⚠️ Where mailboxes are on a provider without app-only admin IMAP, use the
  `imap_password` backend (one app password per mailbox) rather than `m365_oauth`.

## v1.7 — Native email ingestion & clawback hardening ✅ SHIPPED (v18.0.1.7.0)

- ✅ **Phish Alert Button by email** (exportable, native): `bf.reported.phish`
  is now a mail-gateway target. A `mail.alias`
  (`signaler-hameconnage@<domain>`, `alias_contact='employees'`) +
  `message_new` create a report from a **forwarded-as-attachment** message; the
  `.eml` is parsed for `Message-ID`/From/Subject. Reuses the instance's existing
  Courriels app (catchall/alias domain) — no per-tenant connector for reporting.
- ✅ **Internal-sender guard**: external forwards are rejected (anti-DoS /
  anti-criteria-injection), on top of `alias_contact='employees'`.
- ✅ **Hardening** (see `SECURITY.md`): distinct `group_bf_secaware_purge` for
  execute/restore (preview stays manager); `delete` mode requires an exact
  `Message-ID`; configurable **blast-radius cap** with explicit confirmation.

## v1.3 — Privacy & incident response (M–L)

- **Retention & anonymization (Loi 25)** — M. Cron that, after a configurable
  window, anonymizes results (drop IP/UA, hash the partner link) while keeping
  aggregate scores, so historical KPIs survive but personal behavioural data does
  not linger. New `ir.config_parameter` + `action_anonymize`. A genuine
  differentiator for a Québec offering.
- **Real "Phish Alert Button"** — M. Users forward suspicious mail to a
  `mail.alias` (`phish-report@client`) → creates a `bf.reported.phish` record;
  the existing `/phish/<token>/report` already covers sims.
- **Reported-phish triage (PhishER-lite)** — L. `bf.reported.phish` (reporter,
  headers, sender, urls, state new/analyzing/threat/clean/sim) fed by
  `fetchmail`/alias; auto-attribute sims by matching our `token`/UTM so they are
  scored "reported" not escalated. Optional Claude classification via the BF
  `claude -p` CLI pattern.
- **Smishing (SMS)** — M. Add `channel='email'|'sms'` to the template; render a
  short link to `/phish/<token>`; track the same way. Reuse: `sms.sms` /
  `mass_mailing_sms`.
- **Manager / HR escalation** — S. Optional `hr` bridge: map `partner_id` ↔
  `hr.employee`; notify `employee.parent_id` when a report goes high-risk.

## v2.0 — Intelligence & integrations (L)

- **AI-generated lures & landing pages** — L. Generate template body + landing +
  teachable copy via the BF `claude -p` CLI (Max-plan, mounted credentials).
- **Cross-tenant benchmark cohorts** — M. Industry/peer Phish-prone % comparison
  from anonymized BF-hosted-tenant aggregates.
- **Webhook / SIEM stream** — S–M. `base.automation` on result-state changes →
  POST events (reuse the BF ntfy webhook-relay pattern); optional results REST
  endpoint.
- **Directory sync (AD/SCIM/SSO)** — L, mostly out-of-band: rely on Odoo
  `auth_oidc` / existing provisioning rather than build it in-module.

## Explicitly out of scope (low fit for BF's SMB/OBNL clients)

Vishing / IVR, deepfake simulations, callback-phishing, and a self-hosted global
threat-intel blocklist — high cost, low relevance for the target market.

---

## North-star priorities

The four highest-ROI, most Odoo-native investments, in order:
1. **Risk-trend reporting + dashboard** (v1.2)
2. **Recurring programs with smart targeting** (v1.2)
3. **Reported-phishing triage** (v1.3)
4. **Retention / anonymization for Loi 25** (v1.3)

None require leaving the Odoo stack; most reuse `ir.cron`, `mass_mailing`,
`website_slides`, QWeb reports, and patterns already present in the BF suite.
