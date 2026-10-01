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
| 18.0.2.4.0 | Triage IA (one-shot LLM call; routed through the Blue Fox AI bridge, `bf_ai_bridge`, since 18.0.4.5.0) |

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

## Phase 5 features (shipped, 18.0.4.6.0 to 18.0.4.15.4)

**Opt-in, team by team.** Every feature below that sends something to a client,
or acts on a ticket by itself, is switched on in the helpdesk team form (tabs
"SLA et étiquettes automatiques", "Relances", "IA et alertes" and "Banque
d'heures et alertes"). Installing or upgrading the module turns none of them on:
a new team starts with no acknowledgement, no reminders, no survey, no AI triage,
no guard words and no spike alert.

What does change on upgrade, without any setting:

- helpdesk emails use the shared branded layout (see "Branded email threads"
  below), with a named sender (company and team) instead of a bare address;
- reply subjects take the stable form `[HT00042] Original subject`; a mail client
  that groups messages by subject may open a new thread once for a request
  already in progress;
- replies to clients carry a "stop receiving emails for this request" link;
- a reply from the client (email or portal) ends the "waiting for client" state;
- stages that still send the OCA "Ticket closed" email send the branded closing
  email instead.

### Agent workspace and SLA on business hours (18.0.4.6.0)

| Feature | What it does |
|---|---|
| **SLA on business hours** | Each team can carry a working-time calendar (`sla_calendar_id`, the company calendar by default). Response and resolution deadlines are then counted in working hours, calendar leaves included. Without a calendar, deadlines stay in calendar hours, around the clock. |
| **Pause while waiting for the client** | The resolution clock stops while the ticket waits for the client, and the paused hours push the deadline back. The first-response clock never pauses. |
| **First response, measured** | Only a public message from an internal user counts as the first response. An internal note or a message from the client does not. |
| **SLA state** | Stored and indexed: no SLA, on track, at risk (less than 25 % of the time left), paused, breached, met. Badge on the form and list, an "at risk" banner, filters and a group-by. The scheduled job runs hourly and posts the "SLA breached" activity once per ticket. |
| **Team queue** | Menu "File de mes équipes": the open tickets of the agent's teams, nearest resolution deadline first, with filters for waiting tickets, SLA at risk or breached, and recently resolved. |
| **Macros v2** | Variables `{{ client }}`, `{{ prenom }}` (first name), `{{ salutation }}` (the persona's preferred greeting), `{{ numero }}`, `{{ sujet }}`, `{{ agent }}`, `{{ equipe }}`, `{{ portail }}`, `{{ echeance }}`; values are escaped and an unknown variable stays visible. Actions: internal note, stage, tags, assign to me, waiting state. The text stays editable before sending, and a macro with actions only posts nothing. **Collision warning**: if someone else wrote on the ticket since the macro was opened, the wizard shows the latest messages and sends nothing until a second click. |
| **Client 360 tab** | Counted with the agent's own rights: the organization's number of open tickets and its last ten tickets that the agent can see, hour bank balance, last survey answer, overdue invoices of the agent's companies (count and amount, shown only to users allowed to read invoices) and the number of hosted services. Accounting and hosting models are detected at runtime: no added dependency. |
| **Live presence** | "X is also viewing this ticket" on the form, refreshed every 25 s; leaving the ticket or closing the tab is detected. |
| **Keyboard shortcuts** | Alt+O assign to me, Alt+W start or end "waiting for client", Alt+E apply a macro, Alt+D send a client update, Alt+Y AI triage. |

### Client journey (18.0.4.7.0)

| Feature | What it does |
|---|---|
| **Client-facing stage labels** | Each stage can carry a portal label (translatable). The portal shows it instead of the internal stage name, in the list, the ticket page and the stage filter. Empty label = stage name. |
| **Status on the portal** | The portal says when the client's reply is expected, or when the request waits for a third party, with a short explanation. An open ticket with an SLA and no first response yet shows by when the first response is due, in the team calendar's time zone. |
| **Acknowledgement, all channels** | Chosen per team and per channel (web, email, ...). Sent once per ticket, at creation, through the mail queue: it never counts as a first response, and an internal note records it. It carries the request number, the first-response time (with an SLA), the announced business hours (free text, or a summary of the calendar), an optional temporary notice with an end date (holidays, high volume) and the portal link when the client has access. Auto-reply headers are set, and no acknowledgement goes to a closed ticket, to the team alias or the company itself, or more than 5 times per address per hour. Replaces the old `/support/<slug>`-only checkbox. |
| **Waiting-for-client reminders** | Off by default (tab "Relances"). Two reminders, then an announced closing; default delays 3, 4 and 3 days, in business days when the team has a calendar. A public agent reply restarts the cycle; an internal note does not. The automatic closing uses the stage chosen by the team, sends no survey, and a reply from the client reopens the request. Without a closing stage, the agent gets a "no reply from client" activity instead. Per-ticket opt-out. Out-of-office replies neither end the wait nor reopen anything. |
| **Satisfaction survey v2** | One question, five clickable ratings in the email, no Surveys app needed. **Nothing is recorded on click** (mail link scanners open every link): the link opens a page with the rating preselected, and only submitting the form records it. Reasons for a low rating, an optional effort (CES) question and a comment; the client can correct the answer while the link is open. Sent 24 h after closing by default (not if the ticket was reopened), once per ticket, link valid 28 days. A low rating creates a follow-up activity for the team's designated person or the ticket's agent. Report: Reports › Satisfaction (averages by team and month, low ratings, follow-up to do). Per-team mode: none (default), one-question survey, or the previous Surveys-app survey. |
| **Help center** | Help articles (`helpdesk.article`): title, summary, content and keywords, translatable, optionally limited to some teams, published from the website button. Public pages `/aide` (list and search) and `/aide/<slug>`; published articles go to the sitemap. Search ignores accents and stop words and weighs title, keywords, summary and content. Article suggestions appear while the client types the subject, on `/support/<slug>` and on the portal form. "Was this article helpful?" (one vote per session), view counts that skip bots. From a closed ticket, "create a help article" drafts an unpublished article from the client's description and the team's last public reply. No website menu entry is added: link `/aide` where you want it. |

### Branded email threads and accessibility (18.0.4.8.0, 18.0.4.12.0)

| Feature | What it does |
|---|---|
| **One layout for every helpdesk email** | Thread replies, `helpdesk_mgmt` notices, acknowledgement, reminders, closing, survey and client update all render through `bf_helpdesk.mail_layout_helpdesk`: each trigger supplies only its content. Since 18.0.4.12.0 the helpdesk blocks (title and request number, message, button, quoted history, reference, unsubscribe link) are placed inside the shared `bf_onboarding_base.bf_mail_layout`, which `bluefox_branding` replaces with its own layout when installed. Emails that belong to other documents (invoices, quotations) are never touched. |
| **Tenant brand colours, readable** | Colours come from the company's brand settings, never from the code. Button text and link colours are computed to keep a 4.5:1 contrast ratio. |
| **Accessible emails** | `lang` and `dir` set, layout tables marked `role="presentation"`, title as a heading, `color-scheme` declared for dark mode, explicit link texts. |
| **Stable thread subject** | Set once at creation: `[HT00042] Original subject`. Renaming a ticket during triage no longer breaks the client's thread. |
| **Threading by subject** | When a client's mail program drops the threading headers, a message whose subject carries `[HT00042]` joins that ticket, but only if it comes from the client or a follower of the ticket. |
| **Quoted history** | The last three public messages are quoted under each reply. Internal notes never are. |
| **English versions** | The client's language is kept on the ticket (the contact's language, else the language of the form they filled, else the company's). Templates and the computed sentences (deadline, business hours) come in French and English and follow it. |
| **Accessible portal and public form** | Measured against WCAG 2.1 and 2.2 level AA: brand colours served as CSS variables with readable text, visible keyboard focus, form errors announced to screen readers, labels tied to their fields on the portal creation form, named icon buttons, reflow at 320 px without horizontal scrolling. |

### Client notifications and agent notification matrix (18.0.4.9.0)

Without any preference set, notifications behave exactly as before.

| Feature | What it does |
|---|---|
| **Per-contact frequency** | Each contact receives helpdesk replies either at each reply (default) or as one **daily summary** (sent once a day after 8:00 in the contact's time zone, with each request's replies and a link). Set by the client on the portal, above the ticket list, or by the team on the contact form. |
| **Mute one request** | A link at the foot of each reply, signed for that ticket and that recipient, opens a confirmation page; only the button mutes (a POST with CSRF token), so mail link scanners cannot mute anyone. Same page to unmute, and the same button on the portal ticket page. An internal note records each change. |
| **Always delivered** | A request for information (the ticket waits for the client) and the resolution are always sent, muted or not. The portal always shows everything. |
| **Agent notification matrix** | Menu "Mes notifications": for each event (new ticket in my teams, ticket assigned to me, client reply, SLA at risk, SLA breached), a channel (Odoo inbox, email, ntfy, none) and a timing (immediately, hourly digest, daily digest). Each agent sees only their own preferences; the helpdesk manager sees all. Very high priority tickets always notify immediately. ntfy uses the same relay as the critical alert; if the relay is missing or fails, the notification lands in the Odoo inbox instead. Mentions are left to Odoo. |

With `bf_helpdesk_digest` installed, an agent who receives the morning digest gets
their daily email digests inside it (see that module's README).

### AI triage, guard words, spike alerts and themes (18.0.4.10.0)

Principle: the AI suggests, the agent decides. Nothing is written on a ticket or
sent to a client by the AI without a click.

| Feature | What it does |
|---|---|
| **Richer triage** | The triage pass (see "Triage IA" below) now also returns a category (from the helpdesk categories of the ticket's company), a priority, the sentiment (calibrated for support: reporting a problem is not negative), the language and a confidence score; the draft reply is written in the ticket's language. Values outside the allowed lists are dropped. |
| **Automatic triage** | Per team ("Triage IA automatique"): each new ticket is queued and triaged in the background. Ticket creation never waits for the AI bridge; if the bridge is down, the queue waits. |
| **Apply, reject, use the draft** | "Apply the suggestion" opens a wizard where the agent unticks what they do not keep; "Reject" in one click; "Use the draft" opens the composer, nothing is sent by itself. |
| **Accuracy log** | Every outcome (accepted, modified, rejected) is logged with its confidence. Reports › Triage IA: acceptance rate by month and by confidence band. |
| **Guard words** (no AI) | Per team. A ticket containing a guard word (general outage, ransomware, phishing, data breach, hacked, ...) goes to very high priority, gets an escalation tag and an internal note, and triggers the critical ntfy alert if the team has it. Matching ignores case and accents and works on whole words. The list is editable in the system parameter `bf_helpdesk.guard_words`. |
| **Spike alerts** (no AI) | Per team: N tickets from the same organization (or the same category) within X hours notify the team members (and ntfy, when the relay is configured), once per window. Reports › Alertes de pic. |
| **Weekly themes** | Each week, the closed tickets of teams with automatic triage are sorted into themes (`helpdesk.theme`); an existing theme is never renamed, so series stay comparable. Reports › Thèmes: count over 7 days, average of the 4 previous weeks, and a trend only from 10 tickets ("insufficient sample" below). With `bf_helpdesk_merge`, "create a problem" links a theme's recent tickets to a parent incident. |
| **Shared vocabulary** | Helpdesk themes and customer-experience themes (`bf_cx`, analysed by `bf_cx_ai`) are offered to each other's AI pass, so the same irritant carries the same name on both sides, without either module depending on the other. |

Guard words and spike alerts never call the AI bridge; triage and themes need it.

### Closing email (18.0.4.11.0)

A branded, bilingual **"Demande fermée / Request closed"** email: named sender,
the closing stage, and an invitation to reply to reopen the request. It replaces
the OCA "Ticket closed" template on the stages that still use it; a stage set to
another template by hand keeps its choice. It stays silent when the satisfaction
survey goes out at closing (the survey already announces the end of the request)
and when the module sends its own closing message (automatic closing after
reminders, grouped reply from a parent incident).

### Robustness and access (18.0.4.10.0 to 18.0.4.15.4)

- **Scheduled jobs isolated per record**: reminders, surveys, triage queue and
  digests process tickets one by one, each in its own savepoint, so one failing
  ticket no longer blocks the others.
- **Company record rules** on surveys, help articles, themes, the triage log,
  spike alerts and the notification queues: an agent of one company no longer
  sees, or publishes, another company's records.
- **Portal read restrictions**: a portal user can no longer read internal fields
  of their own ticket (triage state, sentiment and draft, guard words, persona,
  hour bank, SLA and reminder internals, time spent, Client 360 counters) nor some
  internal settings of its team: hour bank, push alerts, survey follow-up and
  auto-tag rules. The first-response deadline stays readable: the portal announces it.
- **Onchange guard** (18.0.4.14.0): Odoo checks neither field groups nor record
  rules when a form computes a new record. On the helpdesk models, onchange
  refuses portal users, fields restricted to a group, tickets the user cannot
  read (in values, defaults, per-user defaults and nested lines) and records
  linked by an x2many command that the user cannot read.
- **Wizards belong to their creator**: Odoo 18 applies no implicit rule to
  transient models, so macro, triage, incident and merge wizard rows carry a
  creator-only rule.
- **Persona**: the persona fields of a ticket, searching tickets by persona and
  the persona greeting in macros (`{{ salutation }}`) are limited to the persona
  role of `bf_persona`.
- **Links read with elevated rights are checked when set**: the survey answer of
  a ticket is set only by the survey invitation, and a knowledge-matrix item can
  be linked only by a user who can read it.
- **Context from the caller is not trusted**: the agent-notification dispatcher
  marks its own calls with values a JSON or XML-RPC request cannot produce, so a
  poster cannot force a notification channel or skip the dispatcher.
- A message from an unknown address no longer ends the waiting state, and
  subject-based threading ignores agents' own addresses.
- An ordinary agent can open the full ticket form, even without timesheet or
  meeting rights.
- **Client update guard** (18.0.4.13.0): the composer refuses to send the
  client-update template while its placeholder text ("[Décrivez ici
  l'avancement..." / "[Describe the progress...") is still in the message.

### Requirements and companion modules

- Since 18.0.4.12.0: **`bf_onboarding_base` >= 18.0.2.1.0** (shared mail layout).
- AI triage and themes: a running AI bridge reachable through `bf_ai_bridge`.
- Optional companions:
  - **`bf_helpdesk_merge`**: duplicate suggestions, complete ticket merge, parent
    incidents with grouped reply, "create a problem" from a theme;
  - **`bf_helpdesk_digest`**: a Helpdesk section in the `daily_todo_digest`
    morning email.

## Triage IA

The "Triage IA" button on a ticket sends the ticket subject, description,
available stages, and team members to an LLM and asks for a categorization,
suggested stage, suggested assignee, and a draft first response. The result is
stored on `triage_suggestion_html` and shown in the "Triage IA" tab.

The call is routed through **`bf_ai_bridge`** (a hard dependency), the single
transport every Symbifox module uses to reach the Blue Fox AI bridge service
over a local Unix socket. `bf_helpdesk` holds no API URL, no key and no HTTP
transport of its own.

The bridge serves `POST /helpdesk/triage`: one pass, no session, no tools and no
MCP server. The ticket is untrusted text written by a third party, so it is
handed to a process that has nothing to reach for — a general-purpose agent,
with its system prompt and its write access, is deliberately not used for an
extraction that needs neither.

The team's available stages and members travel with the ticket, and the bridge
checks the answer against those two lists: a stage or an assignee the team does
not carry is dropped rather than shown. A suggestion exists to be applied by
someone in a hurry, which makes it the wrong place for a plausible invented
name. The bridge returns **JSON**, and Odoo assembles and escapes the panel
itself — model-authored HTML is never stored.

**Requirement.** Triage only works where the AI bridge service is running and its
socket is reachable from the Odoo container (system parameter
`bf_ai_bridge.socket`, see the `bf_ai_bridge` README). Without it the button
raises a configuration error; every other helpdesk feature keeps working.

Behaviour when something is off:
- **Bridge socket absent** → configuration error: a popup names the system
  parameter to fix, and the ticket is left exactly as it was.
- **Transport or model error, or an empty answer** → persisted as a soft error on
  the ticket (`triage_state=error`) without raising, so the user can retry.

The Odoo-side timeout is `bf_helpdesk.triage_timeout` (default 120 s); it must
stay above the bridge's own 90 s ceiling, or Odoo gives up on a pass that is
still running.

## License

AGPL-3, inherited rather than chosen: this module is a fork-style extension of
OCA `helpdesk_mgmt`, which is itself AGPL-3.

**Practical limitation on redistribution.** Five of its dependencies —
`bf_hour_bank`, `bf_persona`, `project_knowledge_matrix`, `bf_meeting` and
`bf_home` — are BUSL-1.1. They are structural, not conveniences: the
ticket model carries typed relations into the first three and inherits models
from the last two, so they cannot be detected at runtime and made optional.

The AGPL-3 text therefore applies to this module's own source, but you cannot
exercise the redistribution it grants without also obtaining terms for those
five modules. If that is what you need, [talk to us](https://symbifox.com).

## Changelog

| Version | Change |
|---|---|
| 18.0.4.15.4 | Client 360 shows the client's last survey answer from the native survey too (it read only the legacy `survey` mode and stayed empty in native mode), still limited to the tickets the agent can see. |
| 18.0.4.15.3 | Removing the company of a help article (which would show it on the help center of every company) is limited to helpdesk managers. |
| 18.0.4.15.2 | An agent without project rights can open a ticket whose team has an hour bank (the balance and the scope check are computed with elevated rights). A help article stays in the companies of the user who edits it. A notification preference cannot be handed to another agent. The survey answer of a ticket cannot be cleared by a user either. Opening the persona from a ticket is limited to the persona role. The attachments of a ticket are no longer opened to the website form builder (a migration closes them on existing databases). |
| 18.0.4.15.1 | The survey answer of a ticket can no longer be set by a user (an agent could attach another client's answer and read its score in Client 360). Linking a knowledge-matrix item requires read access on it. The onchange guard also covers knowledge items and survey answers. The persona greeting in macros is limited to the persona role. A timesheet line whose ticket is changed in a form shows the new ticket's client only when the user can read it. |
| 18.0.4.15.0 | The onchange guard also checks default values (context defaults and per-user defaults), nested x2many lines, and records linked by an x2many command. Searching tickets by persona is limited to the persona role (it counted clients by payer quality or tone). The forced notification channel and the dispatcher mark are accepted only in the form the dispatcher sets them. A new timesheet line shows the client of its ticket only when the user can read that ticket. The client's time zone and language are read with elevated rights, so the portal ticket list works for a follower whose client belongs to another company. Some internal team settings (hour bank, push alerts, survey follow-up, auto-tag rules) are not readable from the portal. |
| 18.0.4.14.0 | Access hardening. Onchange calls on the helpdesk models refuse portal users, fields restricted to a group, and tickets the user cannot read (Odoo checks none of these on a new record). Macros render with the agent's rights. Wizard rows (macro, triage suggestion) are visible to their creator only. Client 360 counts only the tickets the agent can see and only the invoices of the agent's companies. Persona fields are limited to the persona role. SLA, reminder, triage-state, theme and time fields are not readable from the portal. Draft help articles taken from a ticket are visible to that ticket's team only. Presence rows are limited to the user's companies. A portal user can no longer trigger agent notifications through the request context. |
| 18.0.4.13.3 | Renaming a team that already has a public form slug now saves the new name (it was silently ignored, along with the other values of the same save). The "apply the AI suggestion" wizard requires write access on the ticket and reads the suggestion with the user's rights. Persona, knowledge-matrix, scope and hour-bank fields of a ticket are not readable from the portal. A timesheet line can only be attached to a ticket the user can read. Live presence on the ticket form is limited to internal users. |
| 18.0.4.13.2 | The macro wizard and on-demand AI triage require write access on the ticket. The macro wizard reads the latest messages of the ticket to warn about collisions, and AI triage writes its suggestion and calls the model; both are now refused for a ticket the user cannot modify (another team's ticket, or a portal client's own ticket). |
| 18.0.4.13.1 | Satisfaction surveys, AI triage logs and spike alerts follow the ticket visibility rules of `helpdesk_mgmt` (team tickets, or all tickets), instead of being readable across teams within a company. The triage log on the ticket form is shown from the "Team tickets" level up, so an agent with "Personal tickets" access can open the full form. |
| 18.0.4.13.0 | The composer refuses to send the "client update" template while its placeholder text ("[Décrivez ici l'avancement..." / "[Describe the progress...") is still in the message, instead of sending it to the client as is. |
| 18.0.4.12.0 | Helpdesk emails are rendered inside the shared **`bf_onboarding_base.bf_mail_layout`**: the `bluefox_branding` layout when that module is installed, `bf_onboarding_base`'s fallback copy of it otherwise. The helpdesk blocks (title and request number, message, button, quoted history, reference, unsubscribe link) become the body of that layout and no longer draw a frame of their own; their colour follows the company brand colour, with the button text recomputed for a 4.5:1 contrast, the shared layout's own button is turned off and the preview text stays the message's. **Requires `bf_onboarding_base` 18.0.2.1.0.** |
| 18.0.4.11.0 | Branded, bilingual closing email **"Demande fermée / Request closed"** (named sender, closing stage, reply to reopen). On install and upgrade it replaces the OCA "Ticket closed" template (`helpdesk_mgmt.closed_ticket_template`) on the stages that still use it; a stage set to another template by hand keeps it. Silent when the satisfaction survey goes out at closing, and when the module sends its own closing message (automatic closing after reminders, grouped incident reply). |
| 18.0.4.10.0 | **AI triage v2**: category, priority, sentiment, language and confidence on top of assignee, stage and draft reply (draft in the ticket's language); per-team automatic triage through a background queue; apply wizard, reject, "use the draft"; accuracy log and Reports › Triage IA. **Guard words** and **spike alerts** (no AI, per team; guard word list in `bf_helpdesk.guard_words`). **Weekly themes** (bridge endpoint `POST /helpdesk/themes`, Reports › Thèmes, trend from 10 tickets) with a vocabulary shared with `bf_cx`. Hardening: scheduled jobs isolated per ticket (one failure no longer blocks the rest); company record rules on surveys, articles, themes, triage log, spike alerts and notification queues; Client 360 invoice totals only for users allowed to read invoices; internal ticket fields unreadable from the portal; an unknown sender no longer ends the waiting state and agents' addresses are ignored by subject threading; one ntfy per guard word hit; "SLA breached" activity posted once; survey and unsubscribe pages bilingual; an answered survey link expires; an ordinary agent can open the full ticket form; nothing enabled by default on a new team. Client email templates get a **named sender** ("company, team" with the team alias or company address), set by the migration only where the original value is still in place. The ticket list action gets the readable path **`/odoo/helpdesk-tickets`** used by links in notifications and digests. The migration **archives orphan helpdesk views**: copies without an xmlid, left by an earlier `helpdesk_mgmt` install, that shadowed the real views and hid every extension (tabs and team settings); they are archived, not deleted. |
| 18.0.4.9.0 | **Client notifications**: per-contact frequency (each reply or daily summary), set on the portal or the contact form; per-request mute through a signed link and a confirmation page (only the button mutes), plus a portal button; requests for information and resolution always delivered. **Agent notification matrix** ("Mes notifications"): event × channel (Odoo, email, ntfy, none) × timing (immediate, hourly, daily); very high priority always immediate; ntfy falls back to the Odoo inbox. Pairs with the new optional module `bf_helpdesk_digest`. Nothing changes until a preference is set. |
| 18.0.4.8.0 | **Branded email threads**: one master layout for every helpdesk email, tenant brand colours with computed readable contrast, accessible markup, stable thread subject `[number] subject`, threading by subject for the client and followers only, quoted history of the last three public messages. **English versions** chosen by the client's language (`client_lang`). **Portal and public form accessibility** (WCAG 2.1 / 2.2 AA target). Migration: thread subjects and client languages backfilled; the module's mail templates are reloaded once, only if none of them was edited by hand. |
| 18.0.4.7.0 | **Client journey**: client-facing stage labels on the portal, waiting status and first-response deadline on the portal, channel recorded on email-born tickets, acknowledgement for every channel (per team, replaces the `/support/<slug>`-only checkbox), waiting-for-client reminders with automatic closing and reopening, a client reply ends the waiting state, satisfaction survey v2 (one question, nothing recorded on click, delayed sending, low-rating follow-up, Reports › Satisfaction), help center (`/aide`, articles, suggestions while typing). Template language follows the client. Removed a stylesheet link the module added to the header of every website page (the browser rejected it; the styles were already in the frontend bundle). Migration: teams that had the old acknowledgement box ticked get the Web channel only, other teams none; old acknowledgement template archived; survey mode set to "survey" for teams with a survey, "none" otherwise. The upgrade sends no email. |
| 18.0.4.6.0 | **SLA on business hours** (per-team calendar, leaves included), resolution paused while waiting for the client, first response counted from public staff messages only, stored SLA state with badges, banner, filters; SLA job hourly. **Agent workspace**: team queue, macros v2 (variables, actions, collision warning), Client 360 tab, live presence, keyboard shortcuts. Access rule added for the macro wizard. Migration: first response backfilled from the chatter, pause opened on tickets already waiting, deadlines and states recomputed, SLA job set to hourly. |
| 18.0.4.5.2 | The portal and public-form stylesheets read the tenant's brand colours (`--brand-primary`, `--brand-dark`) and fall back to the Symbifox palette, instead of a fixed hex value. |
| 18.0.4.5.0 | AI triage moved onto **`bf_ai_bridge`** and the bridge endpoint `POST /helpdesk/triage`. Removed the in-module direct call to the Anthropic Messages API (`_call_anthropic_network`) and its key resolution (`_bf_helpdesk_get_anthropic_api_key`). The endpoint runs one pass with no session, no tools and no MCP server; the answer comes back as JSON, the stage and assignee are validated against the team's own lists, and Odoo renders and escapes the panel. Dropped `bf_claude_chat` from `depends`: nothing in the module read it any more. |
| 18.0.4.4.5 | Brand colour hex values corrected (`#29ABE2` / `#2E3132`). |
| 18.0.4.3.2 | Security: the public form at `/support/<slug>` accepted unlimited anonymous submissions, each one creating a ticket and sending an auto-acknowledgement, so a script could flood the desk and use it as a mail bomb aimed at a third party. Submissions are capped at **5 per IP per 10 minutes**, keyed on the ProxyFix-corrected address; an over-quota post renders the same thanks page, so a genuine over-eager user sees no error and a bot gets no signal. Correctness: `_cron_sla_breach_activity` searched on `sla_response_breach` / `sla_resolve_breach`, non-stored computed booleans that Odoo drops from a domain silently, so the cron was not selecting what it believed. It searches the stored deadline datetimes now and re-checks the breach in Python. The upload deny-list gains `.shtml`, `.xml`, `.xsl` and `.xslt`. |
| 18.0.4.3.1 | Security hardening pass across the repo. |
| 18.0.4.3.0 | Ticket timesheets (`account.analytic.line.ticket_id`, "Feuilles de temps" tab, one-click logging from the chatter; lines land on the ticket project so they deduct from the team hour bank). Branded client update: "Envoyer une mise à jour" preloads the composer with `mail_template_client_update`, editable, never auto-sent. Portal visibility: `/my/ticket` URL and a portal-access indicator on the form, plus "Abonner le client" subscribing the partner as a follower with no invite email. New dependencies: `helpdesk_mgmt_project`, `hr_timesheet`. |
| 18.0.4.2.0 | The suite is decoupled from `bluefox_branding`: `report_brand_{primary,dark,logo}` move to `bf_onboarding_base`, so branded mails, reports and public pages render without the white-label panel installed. |
| 18.0.4.1.2 | AI triage migrated onto the new **`bf_llm`** gateway (added as a hard dependency). Removed the in-module direct Anthropic HTTP call (`_call_anthropic_network`) and the plaintext `bf_helpdesk.anthropic_api_key` / `bf_claude_chat` key resolution (`_bf_helpdesk_get_anthropic_api_key`). Keys are now Fernet-encrypted in `bf.llm.provider`. When no provider is configured the triage button degrades gracefully with a soft notification instead of a hard popup; transient errors still land as `triage_state=error`. ⚠️ This refactor never reached the published code — 18.0.4.4.x still called the API directly — and it is superseded by 18.0.4.5.0, which routes triage through `bf_ai_bridge` instead. |
| 18.0.4.1.1 | `bf_claude_chat` (Gen) downgraded from hard dependency to optional soft-dep — AI triage reads its config via `ir.config_parameter` and degrades gracefully when absent. README cleanup: removed the stale "Phase 3 (planned)" list (all items already shipped) and corrected the CSAT note (uses core `survey`, not `bf_survey_upload`). |
