# Gen - Odoo 18 module

An Odoo module for chatting with Gen, the AI assistant, directly inside the Odoo interface,
through an integrated side panel and a full-screen page.

## Architecture

```
bf_claude_chat/
├── controllers/
│   ├── main.py                  # JSON-RPC + SSE endpoints (/claude-chat/*)
│   └── mobile_api.py            # REST surface for the mobile app
├── models/
│   ├── claude_chat_session.py       # claude.chat.session model
│   ├── claude_chat_message.py       # claude.chat.message model
│   ├── claude_chat_instruction.py   # claude.chat.instruction (steering)
│   └── res_config_settings.py       # Settings (Settings > Gen)
├── security/
│   ├── security.xml             # Access rules (own sessions, admin all)
│   └── ir.model.access.csv      # Model ACLs
├── static/src/
│   ├── js/
│   │   ├── claude_stream.js     # Shared SSE client
│   │   ├── claude_chat.js       # OWL component - full-screen page
│   │   └── claude_systray.js    # OWL component - systray side panel
│   ├── scss/
│   │   └── claude_chat.scss     # Styles (side panel, bubbles, animations)
│   └── xml/
│       └── claude_chat.xml      # OWL templates (ChatAction + SystrayItem)
├── views/
│   ├── menu.xml                 # Main menu + admin (All Sessions)
│   ├── instruction_views.xml    # Steering instructions
│   ├── cockpit_views.xml        # Admin cockpit (sessions + usage)
│   └── res_config_settings.xml  # Settings page
├── tests/
│   └── test_mobile.py           # Bridge framing, models, progress writes
└── migrations/
    ├── 18.0.1.0.0/
    │   └── pre-migrate.py
    └── 18.0.1.4.0/
        └── pre-migrate.py       # Adds res_model/res_id + index
```

## Main components

### Side panel (systray)

- A "Gen" button in the Odoo navigation bar
- Opens as a right-hand side panel (50% of screen width, min 420px, max 800px)
- Semi-transparent overlay, closed with Escape / clicking the overlay / the X button
- **Portal pattern**: the overlay is moved to `<body>` in JS to escape the navbar's stacking context and display above every Odoo element (chatter, statusbar, modals)
- Slide-in animation from the right
- Session list on the left, chat area on the right
- A context badge showing the current page with a pretty name (e.g. "Task #1234 - Name")
- **Per-record filtering**: the systray shows only the conversations linked to the current record (res_model + res_id). The full-screen page still shows every conversation.

### Full-screen page

- Reached through the main "Gen" menu or the panel's expand button
- Session sidebar (280px) plus a centred chat area (max 900px)
- Session renaming by double-click or the pencil icon
- Session archiving (soft delete through the `active` field)

### Context capture

When the panel opens or a new chat is created, the module captures the current
Odoo page's context through 3 cascading strategies:

1. **Router state**: `router.current` (model, resModel, resId, res_id, id, view_type)
2. **URL hash**: parsing `window.location.hash` through URLSearchParams
3. **Action service**: `actionService.currentController.action.res_model`

The display name is taken from:
1. The active breadcrumb (`.o_breadcrumb .active`)
2. The control panel title (`.o_control_panel .breadcrumb-item.active`)
3. The document title (minus the " - Odoo" suffix)

The context is passed to the bridge as `<page-context>` with model, res_id,
display_name, view_type and url.

### Live streaming

Answers stream token by token over Server-Sent Events instead of one blocking
round trip, so tool activity and thinking progress show as they happen and a
timeout keeps the partial answer instead of returning nothing. The route is
`type="http"` (JSON routes cannot stream) and requires the `X-Claude-Stream: 1`
header, which same-origin fetch can set and a cross-site form cannot - a
CSRF-equivalent gate on top of `auth="user"`. Streaming can be turned off in
Settings, in which case the client falls back to the buffered `/claude-chat/send`.

A session whose streamed turns keep failing is **forked** rather than resumed on
the next message (`stream_fail_count`), so a record that got a thread stuck stops
being permanently broken.

### Proactive brief

The first time the panel opens on a record with no conversation yet, a directive
is sent on the user's behalf asking for a short situation report plus next
actions. It is stored as an `internal` message: the assistant sees it, the panel
never renders it.

### Steering instructions

`claude.chat.instruction` holds short directives composed into the system prompt.
An instruction is either global or scoped to one model, and either shared (no
owner) or private to a user. A coherence check reports near-duplicates and
contradictions across the active set.

### Admin cockpit

Under the Gen menu, restricted to `base.group_system`: every session, plus
token and cost counters per turn (pivot, graph and list views).

### Mobile API

`/bf_claude_chat/mobile/v1/*` serves the companion mobile app. A turn is
asynchronous: `/ask` records the question and returns a `turn_id` immediately, a
worker thread consumes the bridge stream and writes progress into the message
(partial text plus a tool log), and the app polls `/turn`. That way a turn that
runs for minutes neither holds a worker on an open SSE connection nor dies with
the phone's screen.

Authentication is a device bearer token resolved through the mobile-device model
of `bf_sms_archive` or `bf_email_management` when either is installed - Gen is
a capability of the existing mobile session, not a third account. Without those
modules the mobile routes simply answer 401.

## Odoo models

### claude.chat.session

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Session title (auto-generated by the bridge) |
| claude_session_id | Char | Claude Code session ID (multi-turn) |
| res_model | Char (indexed) | Linked Odoo model (e.g. project.task) |
| res_id | Integer (indexed) | Linked record ID |
| user_id | Many2one(res.users) | Owner |
| message_ids | One2many | Session messages |
| message_count | Integer (computed) | Message count |
| active | Boolean | Soft archiving |
| origin | Selection (web/mobile) | Where the conversation was started (provenance only) |
| stream_fail_count | Integer | Consecutive streamed failures; past the threshold the thread is forked |
| last_stream_error | Char | Reason code of the last streamed failure |

### claude.chat.message

| Field | Type | Description |
|-------|------|-------------|
| session_id | Many2one(claude.chat.session) | Parent session |
| role | Selection (user/assistant) | Message role |
| content | Text | Content (markdown for the assistant, plain text for the user) |
| internal | Boolean | Directive posted on the user's behalf; never rendered |
| state | Selection (pending/done/error) | Asynchronous mobile turns; defaults to done |
| tool_log | Text (JSON) | Tools used during the turn, in order |
| input/output/cache tokens | Integer | Usage reported by the CLI |
| total_tokens | Integer (stored compute) | Sum of the four counters |
| cost_usd | Float | What the turn would cost at public API rates |
| duration_ms | Integer | Turn duration |

### claude.chat.instruction

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Short label, not sent to the assistant |
| body | Text | The directive itself, composed into the system prompt |
| scope | Selection (global/model) | Every conversation, or one record type |
| model_id | Many2one(ir.model) | The record type, when scoped |
| user_id | Many2one(res.users) | Owner; empty means it applies to everyone |
| sequence / active | Integer / Boolean | Composition order, soft disable |

## JSON-RPC endpoints

Every endpoint is `type="json"`, `auth="user"`, `methods=["POST"]`.

| Route | Description |
|-------|-------------|
| `/claude-chat/send` | Sends a message, returns the assistant's reply |
| `/claude-chat/sessions` | Lists sessions (filterable by res_model/res_id) |
| `/claude-chat/messages` | A session's messages |
| `/claude-chat/rename-session` | Renames a session |
| `/claude-chat/delete-session` | Archives a session |
| `/claude-chat/search-tasks` | Task search (for Share) |
| `/claude-chat/share-to-task` | Posts the conversation into the chatter |

`/claude-chat/stream` is the streaming counterpart of `/send`: `type="http"`,
`auth="user"`, CSRF replaced by the `X-Claude-Stream: 1` header.

The mobile surface is `type="http"`, `auth="public"`, authenticated by a device
bearer token (no session cookie, `save_session=False`):

| Route | Description |
|-------|-------------|
| `GET  /bf_claude_chat/mobile/v1/ping` | Discovery; the version is disclosed only to a recognised device |
| `GET  /bf_claude_chat/mobile/v1/sessions` | The device user's conversations |
| `GET  /bf_claude_chat/mobile/v1/messages` | One conversation's messages |
| `POST /bf_claude_chat/mobile/v1/ask` | Records the question, returns a turn_id |
| `GET  /bf_claude_chat/mobile/v1/turn` | Turn state, partial text and tool log |
| `POST /bf_claude_chat/mobile/v1/delete-session` | Archives a conversation |

## Talking to the bridge

The module talks to the Gen bridge service over a **Unix socket**
(`/run/claude-bridge/bridge.sock` by default). The controller builds a raw HTTP
request on the socket, sends the message together with the user and page
context, and receives the assistant's reply.

The smart title is generated in the background by a daemon thread calling
`/generate-title` on the bridge after the first exchange.

## Configuration (Settings > Gen)

| Setting | Default | Description |
|---------|---------|-------------|
| Enable Gen | True | Turns the chatbot on/off |
| Stream responses | True | Live tokens; off falls back to a buffered reply |
| Brief me when I open a record | True | Proactive situation report on a fresh record |
| Model | sonnet | Claude model (sonnet/opus/haiku) |
| Max Turns | 45 | Maximum tool cycles per message |
| Response Timeout | 660s | Maximum delay for a reply (bridge 600s + 60s buffer) |
| API Key | (empty) | Optional Anthropic key (otherwise the Max plan) |
| Tenant Slug | pme | Selects the bridge's tools and system prompt |
| Bridge Socket | /run/claude-bridge/bridge.sock | Unix socket path |

## Security

- Each user sees only their own sessions and messages (`ir.rule`)
- **Administrators (`base.group_system`) see every session and every message**,
  through the admin rules and the cockpit. Conversations often contain business
  data pasted by the user: treat that access the way you treat mailbox access.
- Users cannot delete messages (`perm_unlink=0`)
- Renaming content is sanitised (HTML stripped, max 120 chars)
- The page context is size-limited (model 64, display_name 200, url 500)
- **The page context is access-checked before it reaches the bridge**: the
  caller's ACL, record rules and multi-company are enforced on the (model,
  res_id) pair, so a crafted context cannot have a record summarised that the
  caller may not read
- Assistant output is sanitised before rendering (tag/attribute allowlist,
  `javascript:`/`data:` hrefs dropped) on both the client and the server
- Headers sent to the bridge refuse CR/LF: the HTTP request is built by hand, so
  a line break would let a caller append headers of their own
- A device bearer token grants the mobile surface **the same write-capable tools
  as the desktop**, acting as the device's user. Revoking a departing user is
  therefore two steps: archive the user (the controller refuses inactive users)
  **and** revoke their device
- The Anthropic API key is encrypted at rest with Fernet, the key coming from
  `BF_CLAUDE_CHAT_FERNET_KEY` or `odoo.conf` - never from the database
- Per-user rate limit of 30 requests/minute, held per worker process

## Technical note: the overlay portal pattern

### The problem

The systray component is rendered inside `.o_main_navbar`, which has
`position: fixed` and a `z-index` (through Bootstrap). In CSS, a positioned
element with a z-index creates a **stacking context**: all its descendants are
confined to that context for z-ordering, even with `position: fixed` and a
maximal z-index.

The consequence: the side panel and its overlay, despite carrying
`z-index: 2147483647`, could not display above elements outside the navbar
(such as the chatter's "Send message" / "Log note" / "Activities" bar, or the
form statusbar), because those participate in a different stacking context
(that of `.o_action_manager` or the root).

### Approaches ruled out

1. **Raising the navbar's z-index** (`z-index: 2147483646 !important`): does not
   address the underlying problem. The navbar's stacking context sits above
   everything else, but the overlay is INSIDE that context, not above it.

2. **`z-index: auto` on the navbar**: would remove the stacking context, but
   `position: fixed` + z-index is required by Bootstrap/Odoo for the navbar to
   stay visible above the content while scrolling.

3. **CSS `body:has(.bf-panel-overlay)` to lower the z-index** of the offending
   bars: fragile, and dependent on Odoo's internal CSS structure, which changes
   between versions.

### The solution: a portal to `<body>`

The portal pattern moves the overlay's DOM node from its OWL location (inside
the navbar) to `document.body` (the document root). In the root stacking
context, `z-index: 2147483647` applies directly and the overlay sits above
every other element.

**Implementation using OWL lifecycle hooks:**

```
DOM after OWL render:            DOM after the portal:

<nav .o_main_navbar>              <nav .o_main_navbar>
  <div .o_menu_systray>             <div .o_menu_systray>
    <button>Gen</button>           <button>Gen</button>
    <div .bf-panel-overlay>  ---->    <!-- bf-overlay-anchor -->
      <div .bf-side-panel/>         </div>
    </div>                        </nav>
  </div>                          ...
</nav>                            <div .bf-panel-overlay>  <-- direct child of <body>
                                    <div .bf-side-panel/>
                                  </div>
```

The challenge is reconciling that move with OWL's virtual DOM, which expects to
find elements where it rendered them. The pattern uses 4 hooks:

| Hook | Action | Reason |
|------|--------|--------|
| `onMounted` | Portal to `<body>` | After the first render, move the overlay |
| `onWillPatch` | Restore into the navbar | Before OWL patches the DOM, put the element back where it belongs so the diff works |
| `onPatched` | Portal to `<body>` | After the patch, move the overlay again |
| `onWillUnmount` | Restore into the navbar | Before the component is destroyed, put the element back so OWL can remove it cleanly |

A `Comment` node (`<!-- bf-overlay-anchor -->`) acts as a placeholder marking
the original position in the OWL DOM, allowing precise restoration before every
patch.

```javascript
// OWL hooks in setup()
const _portalToBody = () => {
    const el = this.overlayRef.el;
    if (el && el.parentNode !== document.body) {
        this._overlayPlaceholder = document.createComment("bf-overlay-anchor");
        el.parentNode.insertBefore(this._overlayPlaceholder, el);
        document.body.appendChild(el);
    }
};
const _restoreFromPortal = () => {
    if (this._overlayPlaceholder && this._overlayPlaceholder.parentNode) {
        const el = document.body.querySelector(".bf-panel-overlay");
        if (el) {
            this._overlayPlaceholder.parentNode.insertBefore(el, this._overlayPlaceholder);
        }
        this._overlayPlaceholder.remove();
        this._overlayPlaceholder = null;
    }
};

onMounted(_portalToBody);
onWillPatch(_restoreFromPortal);
onPatched(_portalToBody);
onWillUnmount(_restoreFromPortal);
```

### Why not an Odoo Dialog/Popover component?

Odoo 18's `Dialog` and `Popover` components use a similar portal mechanism
(rendered into `.o_dialog_container` at body level). However:
- `Dialog` imposes a modal structure (header/body/footer) unsuited to a side panel
- `Popover` is designed for elements anchored to a button, not a full-height panel
- Both add dependencies on Odoo internal components whose API can change

The manual portal is lighter and depends only on the stable OWL API (`useRef`,
`onMounted`, `onPatched`, `onWillPatch`, `onWillUnmount`).

## Deployment

To update:
```bash
# Through XML-RPC (button_immediate_upgrade on ir.module.module)
# Or by restarting the container with the -u flag:
docker exec <container> odoo -c /etc/odoo/odoo.conf -d <db> -u bf_claude_chat --stop-after-init
```

After updating, force a browser asset reload: `Ctrl+Shift+R`.
