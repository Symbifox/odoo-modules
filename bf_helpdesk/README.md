# Symbifox — Helpdesk

Fork-style extension of OCA `helpdesk_mgmt` with native Symbifox integrations.

## Phase 1 features

| # | Feature | Where |
|---|---|---|
| 1 | **Hour bank integration**: per-team `hour.bank.client` link, live balance + low-balance ribbon on tickets | team form › "Hour bank & alerts" tab |
| 2 | **Waiting states**: `waiting_state` on tickets — `Attente — Client.e` / `Attente — Externe`, independent from stage | ticket form/list/search |
| 3 | **Per-team public support form** at `/support/<slug>` — branded BF, replaces `bf_helpdesk_website_form` band-aid | team form › "Public form" tab |
| 4 | **ntfy critical hook**: per-team opt-in, fires POST to webhook relay on Very High (or High) priority tickets | team form › "Hour bank & alerts" tab |

## Configuration

### ntfy webhook URL
Set the system parameter once per deployment:
```
bf_helpdesk.ntfy_webhook_url = http://your-webhook-relay:8090/hook/your-key
```
Then enable `ntfy_critical_enabled` per-team in the team form.

### Public form
1. Open a helpdesk team
2. Set `slug` (e.g. `my-support-team`)
3. Tick `public_form_enabled`
4. Visit `/support/<slug>` — page is anonymous-friendly

## Migration notes

If you have a legacy band-aid module that opted in helpdesk fields for the website
form builder, you can uninstall it after installing `bf_helpdesk` (the opt-in is
folded in via `post_init_hook`). If you have a `base.automation` rule that fires
on helpdesk priority changes, disable it once team-level `ntfy_critical_enabled`
is set, to avoid double notifications.

## Phase 2 features (shipped)

| Version | Feature |
|---|---|
| 18.0.2.0.0 | Persona panel on ticket form (addressing style, tones, payer quality) |
| 18.0.2.1.0 | Spam honeypot + email regex + attachment caps + extension blocklist |
| 18.0.2.2.0 | Knowledge matrix link with scope alignment badge |
| 18.0.2.3.0 | Convert ticket → meeting record |
| 18.0.2.4.0 | Triage IA (one-shot LLM call; now routed through the `bf_llm` gateway) |

## Phase 3 features (shipped)

| Version | Feature |
|---|---|
| 18.0.3.0.0 | CSAT survey auto-sent on close (per-team `survey.survey`, branded BF mail layout) |
| 18.0.3.1.0 | Branded portal templates (Lexend + BF palette on `/my/ticket/<id>`) |
| 18.0.3.2.0 | Dashboard tile on `bf.dashboard` (open/unattended/critical/waiting per team) |
| 18.0.3.3.0 | IMAP gateway hardening (drop autoresponder loops + bulk + bounce subjects) |

## Phase 4 features (shipped)

| Version | Feature |
|---|---|
| 18.0.4.0.0 | SLA per-team (response + resolve hours), breach ribbons on ticket, daily cron drops follow-up activities. **Macros**: reusable canned responses with team scoping, applied via wizard from the ticket header. **Auto-acknowledgement**: branded immediate confirmation email when a ticket is created via `/support/<slug>`. **Auto-tag rules**: per-team regex → tag mapping applied at ticket creation. |
| 18.0.4.3.0 | **Timesheets on tickets** (BF-native, no OCA `helpdesk_mgmt_timesheet` timer stack): `timesheet_ids` + `total_hours` on the ticket, a "Feuilles de temps" tab, and a `ticket_id` link on `account.analytic.line`. Lines land on the ticket's project so they deduct from the team hour bank. One-click time logging from the ticket chatter reuses `bf_chatter_timesheet` (its Composer patch now dispatches by model). New deps: `helpdesk_mgmt_project` (ticket `project_id`/`task_id`) + `hr_timesheet`. **Branded client update**: "Envoyer une mise à jour" header button opens the composer preloaded with the `mail_template_client_update` template (branded via `bluefox_branding`'s composer swap when installed, stock layout otherwise), editable, never auto-sent. **Portal visibility**: ticket surfaces `/my/ticket/<id>` URL + a "client has portal access" indicator, and an "Abonner le client" button subscribes the partner as follower (no invite email sent). |

## Triage IA

The "Triage IA" button on a ticket sends the ticket subject, description,
available stages, and team members to an LLM and asks for a categorization,
suggested stage, suggested assignee, and a draft first response. The result is
stored on `triage_suggestion_html` and shown in the "Triage IA" tab.

The call is routed through the **`bf_llm`** gateway (a hard dependency):
`bf_helpdesk` no longer holds an API URL, key, or HTTP transport. The provider
(Anthropic, OpenAI, or an OpenAI-compatible/local server), the model, and the
Fernet-encrypted key all live in *Settings › Technical › LLM Providers*. The
triage model override is the provider's `model_triage` field. The GenFox
module (`bf_claude_chat`) is **not** a dependency and is no longer read; bf_llm
provides its own encrypted key store.

Behaviour when something is off:
- **No LLM provider configured** → the button degrades gracefully with a soft,
  non-blocking notification; the ticket state is untouched. Every other
  helpdesk feature keeps working.
- **Transient/model error** → persisted as a soft error on the ticket
  (`triage_state=error`) without raising a popup, so the user can retry.

## Changelog

| Version | Change |
|---|---|
| 18.0.4.1.2 | AI triage migrated onto the new **`bf_llm`** gateway (added as a hard dependency). Removed the in-module direct Anthropic HTTP call (`_call_anthropic_network`) and the plaintext `bf_helpdesk.anthropic_api_key` / `bf_claude_chat` key resolution (`_bf_helpdesk_get_anthropic_api_key`). Keys are now Fernet-encrypted in `bf.llm.provider`. When no provider is configured the triage button degrades gracefully with a soft notification instead of a hard popup; transient errors still land as `triage_state=error`. |
| 18.0.4.1.1 | `bf_claude_chat` (GenFox) downgraded from hard dependency to optional soft-dep — AI triage reads its config via `ir.config_parameter` and degrades gracefully when absent. README cleanup: removed the stale "Phase 3 (planned)" list (all items already shipped) and corrected the CSAT note (uses core `survey`, not `bf_survey_upload`). |
