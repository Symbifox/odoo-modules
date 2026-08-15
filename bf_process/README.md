# Process mapping (`bf_process`)

An Odoo 18 module for AS-IS process maps that stay alive. The semantic model —
pools, lanes, nodes, flows — lives in Odoo records. The `.bpmn` file, the
`.drawio` file, the PDF and the on-screen trace are renderings of it, not the
thing itself. All four come out of one layout pass, so they cannot drift apart.

That is the whole point. A process map drawn in a diagramming tool is a
picture: accurate on the day it is drawn, then slowly stops being true. A map
kept as records can be worked on between sessions, argued about in a chatter
thread, versioned, validated, compared, and re-exported whenever someone needs
a file.

## Features

- **Built-in editor**: the current level renders as SVG straight from the
  records — no third-party library, so no license watermark and no outbound
  call. Full screen, zoom (buttons or Ctrl+wheel), pan by drag, SVG download,
  a level switcher, and clickable shapes: a sub-process opens the page it
  expands, any other node opens its own record with its chatter. With write
  access on an unfrozen version, five tools appear: consult, move, place, link,
  remove. See [Editing from the trace](#editing-from-the-trace)
- **Server-side PDF**, 1:1, no browser and no typography engine — see
  [Text measurement](#text-measurement-and-the-server-side-pdf)
- **Two XML exports, because no single tool reads both**: `.drawio` (mxGraph)
  for diagrams.net, the priority target since it is free software under
  Apache-2.0; `.bpmn` 2.0 with its DI part for any editor that reads the
  standard — Lucidchart imports it too, but that is secondary
- **Validation register per activity**: the process owner and a performer sign
  off independently, plus a "disputed" flag when someone says a step does not
  happen that way. A dashboard rolls this up per process (count and
  percentage), and the trace shows a colour dot per node (green validated,
  amber partial, red disputed)
- **Versioning**: duplicate a process into its next version in one click, with
  the same node codes and BPMN identifiers carried over — the validation
  register resets, because a new version needs revalidating
- **Freeze on validation**: a validated version becomes read-only (content,
  not metadata); reopen it explicitly if the validation was premature, and the
  chatter records who did
- **Version comparison**: a wizard lists what was added, removed, renamed, or
  moved between lane, kind, or annotation tone between two versions — matched
  by the stable per-process node codes, not by guesswork
- **BPMN 2.0 import**: reads a `.bpmn` file back into records. Round-tripping
  this module's own export reproduces the same nodes, flows and messages
  (covered by a test); a file from another editor also imports, with its grid
  position reconstructed from the layout, so review it before validating
- **Nodes on `mail.thread` + `mail.activity.mixin`**: each step carries its own
  attachments (shop photos, templates, screenshots), discussion thread and
  activities — a reader can comment without write access to the model

## Dependencies

- `mail`, `project`, `contacts`
- `reportlab` for the PDF backend — already required by Odoo itself, so
  nothing to add to the image
- Lexend (SIL OFL 1.1) is bundled in `static/fonts/`, with its licence. A trace
  that changes font changes its text widths, and the text widths are precisely
  what is frozen in `generateur/lexend_metriques.py`

## How a level is stored

A process (`bf.process`) has levels (`bf.process.diagram`); each level is a
self-contained page with its own layout — lanes, external (black-box) pools,
nodes, sequence flows, message flows. A sub-process node can open a child
level; a shared sub-process can be opened from **more than one** caller, so
the relation runs from the node (`child_diagram_id`) to the page it opens, not
the other way round.

Two things worth knowing about what is stored:

- **Text metrics** — an annotation's height and an external pool's header
  height depend on how wide the text is. Both are computed server-side from a
  calibrated width table (see below). A value carried on the record
  (`node.height`, `diagram.ext_header`) is an **override** and wins: a human
  adjustment survives recomputation, and every map loaded before this became
  computable still exports byte for byte as it did.
- **Emission order** — `sequence` on nodes, lanes, pools, flows and messages
  **is** the order elements are written into the `.bpmn` / `.drawio` file.
  Changing it changes the file produced. The editor keeps it dense (10, 20,
  30…) after every create and delete.

BPMN identifiers (`bpmn_id`) never regenerate, and are unique **per process**,
not globally — a BPMN id only has to be unique within its own file, and a file
is one process.

## The renderers share one geometry

`generateur/geometrie.py` computes lane/pool/node positions once;
`generateur/bpmn.py`, `generateur/mxgraph.py` and `generateur/pdf.py` all
consume it, and the trace's `rendu()` method (`models/serialisation.py`) walks
the same functions. All four outputs — `.bpmn`, `.drawio`, the PDF, and the
on-screen trace — draw from a single layout pass, so there is nothing to keep
in sync by hand.

`tests/test_aller_retour.py` locks this down where it counts: loading a map
into records, exporting it, and re-importing that export reproduces the same
nodes, flows and messages. Off-server checks additionally superimpose the PDF
on the reference rendering and diff the exports against known-good fixtures.

## Text measurement and the server-side PDF

Two heights depend on how wide text renders: an annotation's (how many lines
its text wraps to) and an external pool's header (its longest participant
name). The reference engine measures them with the font. The Odoo image has no
typography engine, so this module carries the measurements instead.

`generateur/lexend_metriques.py` is a **generated** file: for every character
Lexend carries, the glyph advance as the reference engine reports it, at its
exact value. `generateur/mesure.py` reproduces the same sum on the same
numbers — `text_length(s, size) == Σ advances × size` — so it is not an
approximation of the measurement, it *is* the measurement. Regenerate the table
with `tools/etalonner_lexend.py`, which runs **outside the server**, and
re-run the calibration check.

Characters Lexend does not carry but the reference engine resolves through its
own fallback chain — the narrow no-break space that French typography scatters
everywhere, first among them — are recorded in a separate `SUB_*` table,
dated and versioned, because those widths belong to the engine and not to the
font. Anything outside both tables raises `MesureImpossible`, naming the
character. Guessing a width would let the map drift silently.

`generateur/pdf.py` then draws with `reportlab`, at 1:1 — the page is cut to
the map, so the PDF's coordinates are literally the records' coordinates. A
page only scales down if it would exceed the maximum size a PDF page can have,
and then the factor is written in the footer rather than silently applied.

Text is held back and painted after every fill, exactly as the reference engine
does. That is not housekeeping: an annotation background drawn after a label
covers it, and a coordinate-level comparison cannot see it — the word is in the
file, in the right place, just hidden.

## Editing from the trace

Five tools, shown only when the version is not frozen and the user can write:
consult, move, place, link, remove. Every operation is a server call that
returns the whole refreshed trace; the component replaces what it was showing
rather than replaying the move locally.

The model stores no pixels. A node knows its column (progress) and its row
(offset inside its lane); its position in points is computed. So a drag arrives
as a centre in trace coordinates and the server inverts it against the layout
the client is looking at — recomputed from the records, before anything is
written — then snaps to the grid step (`diagram.pas_grille`, 0.05 by default,
the step the generator's own maps use). Without that rounding, every drag would
leave a dirty float in `col` and the exports would drift away from the
reference PDF.

Dropping a node into another lane's band reassigns it, and its row is
recomputed for that lane.

⚠️ One consequence of the geometry, worth knowing before being surprised by it:
a lane's height is its content's, and its topmost node always sits against its
top padding. Moving the topmost node of a lane downwards therefore does not
lower it — the others rise instead. That is the reference generator's geometry,
and the editor shows it as it is rather than papering over it.

## Freeze

A `bf.process` in `state == 'valide'` refuses writes to its content (levels,
lanes, pools, nodes, flows, messages) — see `models/gel.py`. Metadata that
does not change what was validated (state itself, the linked customer/project,
`source`, followers, activities) stays writable. `action_rouvrir()` lifts the
freeze and says so on the chatter; reopening in silence would defeat the
point of freezing at all.

## Version comparison

`bf.process.compare.wizard` indexes both versions by `(level code, node
code)` and diffs level titles, node names/kind/lane/annotation-tone, flow
gate labels, and message labels — additions and removals on every one of
those, in that order. It needs the codes to be stable across versions, which
`action_nouvelle_version()` guarantees by copying the source's node dicts
(via `to_dicts()`) straight into the new process.

## BPMN import

The wizard reads `<bpmndi:BPMNShape>`/`<BPMNEdge>` bounds and reconstructs the
grid: column from x-position divided by the smallest observed horizontal gap,
row from y-position within its lane or pool. This is layout-dependent — it
works because this module's own exporter lays nodes out on a regular grid to
begin with. A hand-drawn or third-party file with irregular spacing will still
import, but the grid it lands on is a best guess: **review before
validating**.

## Security

Two groups, `bf_process.group_bf_process_user` (read, and can discuss on
nodes) and `bf_process.group_bf_process_manager` (full CRUD, BPMN import). 14
access rules across 7 models plus the two transient wizards.

Editing from the trace needs write access on `bf.process.node`, so a reader
sees no tools at all. Each write method checks the freeze *and* the access
right before touching anything, and `rendu()` reports both to the client so a
handle is never offered on something that would refuse it.

## Testing

```bash
odoo -d <db> -u bf_process --test-enable --test-tags bf_process --stop-after-init
```

`tests/test_aller_retour.py` covers load → `to_dicts()` round-trip, both
exporters' output shape, stable BPMN ids across a rename, the annotation
constraint (must be `assoc`-linked to a note), a shared sub-process resolving
to more than one caller, and text measurement: an annotation loaded without a
height gets measured, a longer one gets taller, a carried height still wins,
and a character outside the table is refused by name.
`tests/test_edition.py` covers the pixels→grid inversion (one column width
advances exactly one column), the snap, the lane change, dense sequences after
create and delete, links and their refusals, and the freeze closing the editor
before a handle is ever offered. `tests/test_pdf.py` covers the PDF being
produced, one page per level, the page cut to the map, Lexend embedded, and the
attachment created. `tests/test_quick_wins.py` covers the
validation register and its roll-up, the freeze (write/create/unlink all
refused, then lifted), a new version's node codes and re-exported `.bpmn`
matching the source, the validation register resetting on a new version,
version numbering skipping an already-taken number, comparison detecting a
rename and a removal, comparison reporting zero diffs between identical
copies, and — the property that actually matters — importing this module's
own BPMN export reproduces the same node count, kinds, names, flows, messages
and lane names as the source.

`qa_bf_process.py` (repo root) runs a separate, non-unittest QA pass: static
checks on the manifest/XML/ACL/RPC surface/licence/icon — including that every
write-from-the-trace method goes through the freeze guard — plus live checks
against a running instance: constraints actually posted in PostgreSQL, views
that compile server-side, a loaded reference map's structure, both exports'
SHA-256 matching a known-good reference, the PDF, the editor exercised on the
real map and rolled back, and the backend asset bundle building with the editor
in it.

Two checks need the reference engine and therefore live outside the module,
next to that harness:

- `qa_mesure.py` — every character of the frozen table, at several sizes,
  against the font itself; the substitutes; a corpus of real strings; the line
  wrapping; both derived heights; and the refusal.
- `qa_pdf.py` — takes the maps **as they are in the database**, renders each
  level twice (this module, then the reference engine) and overlays them:
  page size, every stroke and curve at its coordinates, every word and its box,
  and the two pages rendered to image and compared tile by tile. The image pass
  is what catches what coordinates cannot: stacking order, fills, stroke
  widths, dash patterns.

## Notes

- No third-party diagramming library. `bpmn-js` was evaluated and not adopted:
  its enhanced-MIT license requires a visible, unmasked bpmn.io watermark even
  in commercial/internal use, which is a licensing decision to make
  deliberately, separately from "can we edit the map". The editor here is a few
  hundred lines of SVG and five server methods.
- Text measurement covers the characters Lexend carries, plus a recorded
  fallback set. A character outside both — an emoji, say — makes the
  measurement refuse rather than guess.
