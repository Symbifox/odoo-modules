# Changelog - Gen (bf_claude_chat)

## 18.0.1.21.0 - 2026-09-14

### Pendant que Gen travaille, l'écran dit ce qu'il fait

Un tour de Gen dure 97 s en médiane au bureau, et pendant ce
temps la bulle affichait trois points, un compteur « 212 tokens » en anglais
qui repartait à 50 à chaque bloc de réflexion, et une pastille par outil :
12 par tour en médiane, dont 70 % disaient « Bash ».

La bulle porte maintenant une **ligne d'état** et un **chrono** : « Lecture de
la tâche · 42 s ». Quand un outil tourne, la ligne dit lequel, en français, et
reprend la description que Gen écrit pour sa commande dès que le pont la
relaie (« Compter les fiches encore ouvertes »). Quand Gen réfléchit sans
outil, elle tire une formule du renard (« Gen flaire la piste », « Gen fait le
guet »), puisque le flux du CLI ne livre pas le contenu de la réflexion. Les
pastilles sont repliées en un compteur « 13 étapes » qu'on déplie, et qui
rappelle la dernière étape pendant que Gen réfléchit.

Le fil mobile range la description de chaque outil dans `tool_log` (clé
`detail`), pour que l'app puisse l'afficher à son tour.

⚠️ Demande le pont du 2026-09-14 ou plus récent pour les descriptions
(événement `tool_detail`). Avec un pont plus ancien, la ligne d'état retombe
sur le libellé générique de chaque outil, sans rien casser.

## 18.0.1.20.0 - 2026-09-11

### La personnalité de Gen se règle dans Odoo, le reste non

La personnalité de Gen vivait dans un fichier d'invite par
locataire, sur l'hôte, modifiable seulement en shell : un admin client ne
pouvait ni tutoyer ni raccourcir son assistant. Paramètres › Gen porte
maintenant un champ **« Personnalité de Gen »** : le nom qu'il porte, tu ou
vous, la langue par défaut, la longueur, la façon dont il se présente. Le
contrôleur l'envoie au pont dans `context.identity`, avant les consignes, sur
le bureau comme au mobile. Le pont le rend en bloc `<identity>` et le dit pour
ce qu'il est : le ton, jamais les capacités. Ce que Gen peut faire (outils,
confirmation avant tout envoi, cloisonnement) reste dans l'invite du pont, hors
de portée d'une zone de texte. Plafond de 2 000 caractères, **refusé** à
l'enregistrement plutôt que coupé : le pont coupe à la même longueur, et une
coupe serait silencieuse.

### Le bloc de consignes ne se coupe plus au milieu d'une phrase

Le bloc composé partait tronqué à 4 000 caractères des deux côtés, contrôleur
et pont, sans que rien ne le dise. Blue Fox est à 3 029 avec 13 consignes ; la
quatorzième aurait été coupée en plein mot. Le bloc est maintenant ajusté sur
une **frontière de consigne** (les dernières dans l'ordre des séquences
restent dehors), la coupe est journalisée, et chaque fiche affiche **« Place
occupée »** : la taille du bloc pour son public, elle comprise, contre le
plafond. Écrire une consigne qui ferait déborder le bloc lève un avertissement
dans le formulaire ; le contrôle de cohérence ouvre sur la jauge du bloc
global.

Douze tests neufs, dont un qui vérifie que la jauge compte le brouillon en
cours et ne compte pas deux fois la consigne qu'on modifie.

## 18.0.1.19.2 - 2026-09-11

### Trois choses que l'écran disait de travers

**2800 %.** La colonne d'utilisation portait `widget="percentage"`, qui
**multiplie par cent** : le serveur rend déjà des pourcentages, donc 28 se
lisait 2800 % et 7 se lisait 700 %. Le widget est retiré ; l'unité est dans le
libellé du champ.

**La bascule affichée un jour trop tard.** Les dates étaient justes en base
(13:00 et 07:00 UTC) mais un `Datetime` se rend toujours dans le fuseau de la
fiche d'usager, et une fiche réglée à `Pacific/Auckland` le montre bien. La bascule du **vendredi 03:00 heure de Montréal**
s'affichait donc « vendredi 19:00 », soit l'inverse de ce que la mesure veut
dire. Un champ `bascule_montreal` la rend en clair dans le fuseau de référence,
quel que soit le lecteur ; l'horodatage brut reste disponible en colonne
optionnelle. Quatre tests le vérifient sous Auckland, Toronto, UTC et sans
fuseau, et un cinquième sur une date de janvier pour que l'heure avancée ne soit
pas écrite en dur.

**« Dans les clous ».** Il a fallu demander ce que ça voulait dire, ce qui était la
réponse. L'état se lit maintenant « Sous les seuils », et « Rien à dire » est
devenu « Sans relevé utilisable », le même mot que le filtre de recherche. Les
clés techniques n'ont pas bougé.


## 18.0.1.19.1 - 2026-09-11

### Les seuils vivent au compte, donc la sonde doit pouvoir les lire

`seuils_du_compte(config_dir)` rend les quatre seuils d'un compte, en sudo comme
`enregistrer_releve`.

La sonde tourne sous l'identité du robot (« Gen », uid 1021 sur BF), qui n'est
**pas** administrateur : les écrans restent réservés aux admins, c'est la
demande. Elle ne peut donc rien lire de `claude.account`, alors qu'elle a besoin
des seuils pour juger. Sans cette méthode, ils vivraient en double, dans la ligne
de cron **et** dans la fiche, et les deux dériveraient sans que personne le voie.
La règle de jugement reste dans la sonde ; seuls les nombres viennent d'ici.

Un compte inconnu rend un dictionnaire **vide** et non une erreur : à la sonde de
retomber sur ses valeurs par défaut plutôt que de se taire. Un compte **archivé**
rend quand même ses seuils, parce qu'archivé veut dire « plus à l'écran », pas
« plus mesuré ».


## 18.0.1.19.0 - 2026-09-11

### Quel compte a payé, et ce qu'il lui reste

Le registre disait **quelle fonction** avait dépensé (`origin`, depuis la
18.0.1.17.0), jamais **quel compte** avait payé. C'est une dimension qui ne se
déduit d'aucune ligne existante : un locataire peut tirer sur l'abonnement de
Blue Fox, un autre sur le sien.

`claude.account` porte donc le compte, son forfait, ses seuils et l'état de sa
dernière lecture. `claude.chat.session` gagne `account_id`, et
`claude.chat.message` en garde une copie stockée, comme il le fait déjà pour
l'usager et le modèle. `journaliser_passe` accepte un `compte` facultatif : un
appelant qui ne le sait pas laisse le fil non attribué, ce qui est une réponse
honnête et pas une perte.

**Deux tables plutôt qu'une, et ce n'est pas de la décoration.** Les fenêtres
(session de 5 h, semaine, semaine Opus) vivent dans `claude.account.window`, une
ligne par fenêtre **réellement mesurée**. Dans un modèle à colonnes, une fenêtre
absente du relevé vaudrait `0.0` en base, et un tableau de bord la lirait « rien
consommé » au lieu de « non mesuré » : exactement l'erreur déjà payée sur le
registre, où des lignes rendues à zéro se lisaient « gratuit ». Le vrai relevé
rend d'ailleurs ses fenêtres inactives à `None`, pas absentes, ce qui aurait
rempli la table de faux zéros.

**Admins seulement.** Ce que ces écrans montrent, c'est ce qu'un abonnement a
encore sous le pied : ça ne regarde pas les usagers, et sur un locataire client
ça regarde encore moins le client. Un test vérifie aussi que fermer le compte ne
ferme pas le clavardage, puisque `claude.chat.message.account_id` est un champ
related que tout le monde lit.

La mesure, elle, ne peut pas vivre ici : les identifiants et les transcripts sont
sur l'hôte, et les comptes sont partagés par des conteneurs et des crons qui ne
touchent jamais Odoo. `scripts/check_claude_token_budget.py` relève et verse par
`enregistrer_releve`, jamais bloquant, comme `journaliser_passe`.

### Trois pièges fermés en chemin

**`fields.Datetime.to_datetime` ne lit pas l'ISO 8601 du relevé.** Elle attend le
format serveur (« AAAA-MM-JJ hh:mm:ss ») et rend `False` sur un « T » avec
fuseau, ce que le relevé rend toujours. L'erreur ne se voyait nulle part : elle
vidait seulement toutes les dates de bascule, et avec elles la moitié « solde
dormant » de la mesure. Deux tests sont tombés là-dessus avant la correction.

**Un `Float` Odoo ne sait pas valoir « inconnu ».** `heures_restantes` rend 0.0
quand la remise à zéro manque, et 0.0 passe sous n'importe quel préavis : le
jugement aurait crié « solde dormant » sur toute fenêtre sans date. La garde
porte donc sur `resets_at`.

**Un calculé non stocké se cherche avec `search=`, ou il rend tout.** L'état d'un
compte dépend de l'heure (la bascule approche, ou pas), donc il ne peut pas être
stocké sans vieillir en silence. Sans méthode de recherche, un filtre posé
dessus aurait rendu TOUTE la table en ayant l'air de filtrer.

### Ce qui n'est pas fait

Le relevé porte un `seven_day_breakdown` qui dit ce qui mange la semaine (Claude
Code, clavardage, cowork, autre) et un tableau `limits[]` avec des fenêtres par
modèle. Rien de tout ça n'est ingéré : c'est une autre table et une autre
décision.

### Fichiers

| Fichier | Changement |
|---|---|
| `models/claude_account.py` | nouveau : `claude.account` et `claude.account.window` |
| `models/claude_chat_session.py` | `account_id` |
| `models/claude_chat_message.py` | `account_id` related stocké, `compte` sur `journaliser_passe` |
| `views/account_views.xml` | nouveau : liste, formulaire, recherche, menu admin |
| `security/ir.model.access.csv` | deux lignes, `base.group_system` seulement |
| `tests/test_comptes.py` | nouveau : 31 tests, dont le relevé réel du 2026-09-11 |


## 18.0.1.18.0 - 2026-09-06

### Gen follows the dark theme instead of fighting it

Gen painted its own surfaces with hard-coded light colours -- `#f8f9fa` for the
page, `white` for the sidebar and the side panel, `#fafafa` for the headers,
`#f4f4f5` for code blocks. None of them is a Bootstrap `.card` or an Odoo view,
so **nothing in `bf_dark_mode` ever repainted them**: with dark mode on, the
whole of Gen stayed light inside a dark client.

The text was worse than the background. `bf_dark_mode` forces `p`, `h1..h6`,
`strong` and `.text-muted` client-wide, so an answer's own prose was written in
the dark theme's pale greys **on Gen's light bubble**:

| element (dark mode, before)  | foreground | background | contrast |
|------------------------------|-----------|------------|----------|
| paragraph of an answer       | `#d1d5d8` | `#eef8fd`  | **1.37:1** |
| bold / heading of an answer  | `#e8eaec` | `#eef8fd`  | **1.12:1** |
| conversation date            | `#8e9496` | `#ffffff`  | 3.08:1   |
| empty state of the panel     | `#8e9496` | `#f8f9fa`  | 2.92:1   |

Every surface colour now goes through a `--bf-gen-*` custom property, declared
on `:root` with **exactly** the previous values and redeclared in one block
under `body.bf_dark_mode`. Once the surface is dark, the theme's pale text is
correct rather than wrong -- so the fix is a repaint, not a pile of overrides.

Three things needed more than a token:

- **The text colour goes on the surface.** Gen renders inside
  `.o_action_manager`, not `.o_content`, so the only rule that lightens
  inherited text never reaches it and `<body>` stays at `#495057` even in dark
  mode. `.bf-chat-container` and `.bf-side-panel` now set `color` themselves.
- **The brand accent is never foreground text on dark.** `#29ABE2` on `#2E3132`
  is ~3.4:1. Links, context badges, tool chips and avatars use a lightened
  accent (`color-mix(brand 45%, white)`, 6.6:1 to 11:1 across Gen's surfaces);
  the raw accent stays for fills, borders and focus rings.
- **The theme's muted grey is too dark for Gen.** `#8e9496` gives 3.27:1 on the
  raised surface, and conversation dates are 0.75 rem. Inside Gen only, muted
  text is `#b0b6b8` (4.9:1 or better everywhere).

No manifest dependency was added: the `body.bf_dark_mode` selector simply never
matches on a tenant without the theme module.

Light mode is untouched -- the same page, captured before and after the change,
is byte-for-byte identical.

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
