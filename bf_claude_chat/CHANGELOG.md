# Changelog - GenFox (bf_claude_chat)

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
