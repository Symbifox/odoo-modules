# Changelog - Gen (bf_claude_chat)

## Bridge fix - 2026-08-30 (outside the module version)

### Utterances no longer weld together, and narration is no longer thrown away

Fixed in the bridge service (`_chat_stream_gen`). No module file changed: the
correction travels in the SSE stream, so the side panel, the full-screen page
and the mobile app all benefit without a line of JS. Recorded here because Gen's
visible behaviour changes.

A turn where the assistant announces what it will do, calls a tool, then
comments on the result produces **several text blocks**. Two opposite defects
were hiding each other:

- **In the stream**, the CLI puts no separator between two blocks, so the
  utterances welded together: `...the record.Here is what I found`.
- **At storage time**, the `result` field of the final event carries only the
  **last** block. All the narration before the tool calls was thrown away and
  replaced on screen what the user had just watched stream by.

Neither view was complete, and the difference between the two read as a display
glitch.

A paragraph break is now inserted when a text block opens after another one, and
the accumulated text is preferred over `result` only when it **ends with**
`result`, meaning it is a strict superset. Otherwise `result` wins: no blind
substitution.

⚠️ The `usage` block of the `result` event carries the FULL turn. Accounting
from `usage` is correct; accounting from the text of `result` is not.

## v18.0.1.17.1 - 2026-08-30

### The ledger finally counts every pass, not just the chat

Since 2026-08-09 every chat turn records what it consumed: input and output
tokens, cached context, re-read context, API-equivalent cost and duration. What
the ledger did not say is that eight other features spend through the same
bridge without recording anything: meeting refinement, agenda refinement,
meeting review, the editorial workshop, process mapping, invoice and card OCR,
contact enrichment, title generation. Those are the longest passes, and the
ledger read as though the assistant were only ever used for chatting.

The bridge already computed their consumption and threw it away. This version
opens the entry point through which it records it:
`claude.chat.message.journaliser_passe(...)`.

**The Origin field (`origin`) changes meaning.** It used to say where the
conversation was held, web or mobile, which has meant nothing since mobile
parity in 18.0.1.11.0: both go through the same `/chat-stream`. It now says
**which feature did the spending**. That is the dimension needed to answer the
real question: not how many tokens, but what they bought.

**One thread per record worked on**, not one giant thread per feature. Refining
meeting 341 gets its own, meeting 342 gets its own. It costs the same number of
rows and keeps `res_model` / `res_id`, so attaching a pass to a project or a
task stays possible later. A single thread per feature would have made that
impossible without a data migration.

### The transport moved out to `bf_ai_bridge`

The hand-written HTTP frame over the Unix socket no longer lives here. It moved
to the bare leaf module `bf_ai_bridge` (LGPL-3, `base` as its only dependency),
which now also carries the single system parameter for the socket path,
`bf_ai_bridge.socket`. The old keys `bf_claude_chat.bridge_socket` and
`bf_meeting.bridge_socket` are removed by the 18.0.1.16.0 migration.

⚠️ On a tenant where Gen is **not** installed, that migration never runs: remove
the old key by hand after the switch.


## v18.0.1.15.4 - 2026-08-30

### The assistant is now called Gen

The public name goes from "GenFox" to "Gen". A first name is easier to
remember, reads the same in French and English, and steps out of an already
crowded "fox" family (Blue Fox, Symbifox, `bf_`).

What changes: the module name, the menu, the systray button, the panel header,
the Settings page, the input placeholder, the fr_CA translations - everything a
user reads.

What deliberately does not change: the technical name stays `bf_claude_chat`,
along with every identifier (`genfox_*`, `action_genfox_*`), the notification
channels and the source comments. **GenFox remains the internal code name; Gen
is the public one.** No column added, no migration.

## v18.0.1.13.0 - 2026-08-16

Consolidated release covering everything since v18.0.1.5.2.

### Live streaming

Answers now stream token by token over Server-Sent Events (`/claude-chat/stream`,
`type="http"`, CSRF replaced by a required `X-Claude-Stream: 1` header), with tool
activity and thinking progress shown as they happen. A timeout keeps the partial
answer instead of returning nothing. Streaming can be switched off in Settings,
and the client falls back to the buffered `/claude-chat/send`. A session whose
streamed turns keep failing is forked rather than resumed on the next message.

### Mobile API

`/bf_claude_chat/mobile/v1/*` serves the companion mobile app, with the same
tools and the same session as the desktop, so a conversation started on the phone
carries on at the desk. A turn is asynchronous: `/ask` returns a `turn_id`, a
worker thread consumes the bridge stream and writes progress into the message,
and the app polls `/turn`. Authentication is a device bearer token borrowed from
`bf_sms_archive` or `bf_email_management`; without either module the routes
answer 401.

### Steering instructions

New `claude.chat.instruction` model: short directives composed into the system
prompt, global or scoped to one model, shared or private, with a coherence check
that reports near-duplicates and contradictions.

### Proactive brief

Opening the panel on a record with no conversation yet asks for a situation
report and next actions, stored as an internal message the panel never renders.

### Admin cockpit and usage counters

Sessions, token counts and API-equivalent cost per turn, in list, pivot and graph
views, restricted to `base.group_system`.

### Security

- The page context is access-checked before it reaches the bridge: the caller's
  ACL, record rules and multi-company are enforced on the (model, res_id) pair,
  so a crafted context cannot have a record summarised that the caller may not
  read.
- Headers sent to the bridge refuse CR/LF, since the HTTP request is built by
  hand; covered by tests.
- The mobile controller refuses a device whose user has been archived.
- `/ping` no longer discloses the installed version to an unauthenticated caller.
- The Anthropic API key stays encrypted at rest (Fernet, key from the environment
  or `odoo.conf`, never the database).

## v18.0.1.4.1 - 2026-03-20

### Fixing the overlay hidden behind the chatter (portal pattern)

**The problem**: the GenFox side panel displayed behind Odoo's chatter bar
("Send message", "Log note", "Activities") and the form statusbar. Despite a
`z-index: 2147483647` on the overlay, it was confined to the navbar's stacking
context (`position: fixed` plus a z-index creates an isolated CSS stacking
context).

The previous approach (raising `.o_main_navbar`'s z-index to 2147483646 through
`:has()`) did not address the underlying problem: a `position: fixed` descendant
cannot escape its ancestor's stacking context.

**The solution**: an OWL portal pattern that moves the overlay's DOM node to
`document.body` after every render, and restores it before every patch for
compatibility with OWL's virtual DOM.

- `onMounted` / `onPatched`: moves `.bf-panel-overlay` to `<body>`, inserting a
  `Comment` node (`<!-- bf-overlay-anchor -->`) as a placeholder
- `onWillPatch` / `onWillUnmount`: restores the overlay to its original position
  so the OWL diff works correctly
- Removal of the `.o_main_navbar:has(.bf-panel-overlay) { z-index: 2147483646 }`
  CSS hack

The overlay now participates in the root stacking context, guaranteeing it
displays above every Odoo element with no dependency whatsoever on Odoo's
internal CSS structure.

See the README's "Technical note: the overlay portal pattern" section for the
details.

### Files changed

| File | Changes |
|------|---------|
| `static/src/js/claude_systray.js` | OWL hook imports (onMounted, onPatched, onWillPatch, onWillUnmount), portal pattern added, overlay t-ref |
| `static/src/xml/claude_chat.xml` | `t-ref="panelOverlay"` added on `.bf-panel-overlay` |
| `static/src/scss/claude_chat.scss` | Navbar z-index hack removed, comment updated |
| `README.md` | "Overlay portal pattern" technical section plus an updated panel description |

---

## v18.0.1.4.0 - 2026-03-18

### Sessions filtered by the current record

**The problem**: clicking GenFox in the systray showed EVERY conversation.
What you want is only the ones tied to the current record (the one you are
looking at).

**The solution**:
- Added `res_model` (Char, indexed) and `res_id` (Integer, indexed) to the
  `claude.chat.session` model
- A DB migration (`pre-migrate.py`): columns plus a composite index
- The context is stored when a session is created (the Odoo record's model plus
  res_id)
- `list_sessions` filters on `res_model`/`res_id` (backward-compatible: with no
  params, everything)
- The systray reloads the sessions on each opening (the page context can change)
- Post-send refresh with the same filter
- Empty state: "No chats for this record" when the filter is active and there
  are 0 results
- The full-screen page is unchanged (it shows every conversation)

### Fixing timeouts on complex requests

**The problem**: complex requests (a 95+ item matrix, NC cross-referencing,
multi-tool) exceeded the time limits.

**Causes and fixes**:
1. The `max_turns` fallback in the controller was 10 (versus 25 in the bridge) — **fixed to 25**
2. `CLAUDE_TIMEOUT` was 300 s — **raised to 600 s** (bridge) / 660 s (controller, a 60 s socket buffer)
3. The MCP per-tool timeout was 30 s — **raised to 120 s** (bf and pme configs)
4. There was no XML-RPC timeout — **added `TimeoutTransport` (60 s)** in `clients/odoo_client.py`
5. The default NC timeout was 15 s — **raised to 30 s** in `clients/nextcloud_client.py`

### Fixing HTML rendering in share_to_task

**The problem**: HTML tags displayed as plain text in the Odoo chatter when
sharing.

**The solution**: added `body_is_html=True` to `task.message_post()`
(share_to_task).

### Better HTML detection in the bridge

**The problem**: `_has_html()` only detected block tags, missing the abundant
inline HTML.

**The solution**: broadened detection — block tags OR (more than 2 occurrences
of `<` plus at least one HTML tag).

### Files changed

| File | Changes |
|------|---------|
| `__manifest__.py` | Version 18.0.1.3.0 -> 18.0.1.4.0 |
| `models/claude_chat_session.py` | res_model + res_id added |
| `models/res_config_settings.py` | Default timeout 300 -> 660 |
| `controllers/main.py` | Context storage, filtered sessions, max_turns fix, share HTML fix, timeout |
| `static/src/js/claude_systray.js` | Session filtering, reload on each opening, context empty state |
| `static/src/xml/claude_chat.xml` | "No chats for this record" empty state |
| `migrations/18.0.1.4.0/pre-migrate.py` | New: columns plus index |
| `bridge/server.py` | TIMEOUT 600, broader _has_html() |
| `bridge/claude-chatbot-bridge.service` | CLAUDE_TIMEOUT=600 |
| `bridge/mcp_config_bf.json` | timeout 120 |
| `bridge/mcp_config_pme.json` | timeout 120 |
| `clients/odoo_client.py` | TimeoutTransport (60 s) |
| `clients/nextcloud_client.py` | timeout 30 s |

---

## v18.0.1.3.0 - 2026-03-05

### A side panel (replacing the dropdown)

**The problem**: the systray widget used Odoo's `<Dropdown>` component, which
opened a small 480 px popup. Too small for comfortable use, and the dropdown
closed at the slightest click outside it.

**The solution**: a complete replacement by a fixed side panel sliding in from
the right.

- Width: 50% of the viewport (min 420 px, max 800 px), height 100vh
- A semi-transparent overlay (rgba 0,0,0,0.15) behind the panel
- Closed by: the Escape key, a click on the overlay, the X button
- A `translateX` CSS animation for the slide-in (0.2 s ease-out)
- The dependency on Odoo's `Dropdown` component removed
- OWL `useEffect` imported to manage the Escape listener

### Fixing the z-index

**The problem**: Odoo's chatter bar ("Send message", "Log note", "Activities")
positioned itself over the GenFox panel, blocking the view.

**The solution**: the z-index raised to 100000 (versus ~1060 for Odoo's highest
elements).

### Better context capture

**The problem**: `router.current` does not always return `model` and `resId` in
Odoo 18, depending on the view type and the navigation. The page context was
therefore not always detected, and the context badge did not appear.

**The solution**: 3 cascading strategies for capturing the context:

1. `router.current` — the `model`, `resModel`, `resId`, `res_id`, `id` properties
2. Parsing the URL hash through `URLSearchParams(window.location.hash)`
3. `actionService.currentController.action.res_model` through Odoo's action service

The display_name is taken from (in order of precedence):
1. `.o_breadcrumb .active`
2. `.o_control_panel .breadcrumb-item.active`
3. `document.title` (minus " - Odoo")

The context is now re-captured every time the panel opens AND on every "New
Chat".

### "Pretty" context names

**The problem**: the context badge showed the raw model name (`project.task`) or
just the display_name, with no clear indication of the record type.

**The solution**: a `MODEL_LABELS` mapping for the common models:

| Model | Label |
|-------|-------|
| project.task | Tache |
| project.project | Projet |
| helpdesk.ticket | Ticket |
| res.partner | Contact |
| account.move | Facture |
| sale.order | Commande |
| crm.lead | Opportunite |
| knowledge.article | Article |

The badge now shows: "Tache #1234 - The task's name"

### The URL in the bridge context

**The problem**: the page's full URL was not passed to the bridge, which limited
Claude's ability to reference the exact page.

**The solution**:
- The JS captures `window.location.href` and includes it in the context payload
- The Odoo controller passes `url` (max 500 chars) to the bridge
- The bridge includes `url:` in the dynamic prompt's `<page-context>` tags
- A relaxed condition: the context is passed when `model` OR `displayName` is
  available (previously only `model` was required)

### Files changed

| File | Changes |
|------|---------|
| `static/src/js/claude_systray.js` | Rewritten: side panel, multi-strategy context capture, MODEL_LABELS, useEffect |
| `static/src/xml/claude_chat.xml` | Systray template rewritten: side panel instead of Dropdown |
| `static/src/scss/claude_chat.scss` | Side panel styles (overlay, animation, z-index 100000), systray classes replaced |
| `controllers/main.py` | `url` field added to the bridge context |
| `__manifest__.py` | Version bump 18.0.1.2.0 -> 18.0.1.3.0 |
