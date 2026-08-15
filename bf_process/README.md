# Process mapping (`bf_process`)

An Odoo 18 module for AS-IS process maps that stay alive. The semantic model —
pools, lanes, nodes, flows — lives in Odoo records. The `.bpmn` file, the
`.drawio` file, the on-screen trace and the PDF are renderings of it, not the
thing itself.

That is the whole point. A process map drawn in a diagramming tool is a
picture: accurate on the day it is drawn, then slowly stops being true. A map
kept as records can be worked on between sessions, argued about in a chatter
thread, versioned, validated, compared, and re-exported whenever someone needs
a file.

## Features

- **Built-in viewer**: the current level renders as SVG straight from the
  records — no third-party library, so no license watermark and no outbound
  call. Full screen, zoom (buttons or Ctrl+wheel), pan by drag, SVG download,
  a level switcher, and clickable shapes: a sub-process opens the page it
  expands, any other node opens its own record with its chatter
- **Two exports, because no single tool reads both**: `.drawio` (mxGraph) for
  diagrams.net, the priority target since it is free software under
  Apache-2.0; `.bpmn` 2.0 with its DI part for any editor that reads the
  standard — Lucidchart imports it too, but that is secondary
- **Validation register per activity**: the process owner and a performer sign
  off independently, plus a "disputed" flag when someone says a step does not
  happen that way. A dashboard rolls this up per process (count and
  percentage), and the viewer shows a colour dot per node (green validated,
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

## How a level is stored

A process (`bf.process`) has levels (`bf.process.diagram`); each level is a
self-contained page with its own layout — lanes, external (black-box) pools,
nodes, sequence flows, message flows. A sub-process node can open a child
level; a shared sub-process can be opened from **more than one** caller, so
the relation runs from the node (`child_diagram_id`) to the page it opens, not
the other way round.

Two things the server does not attempt to compute, and stores instead:

- **Text metrics** — the Odoo image ships no PDF/typography engine, so an
  annotation's height and an external pool's header height are *carried on the
  record* (`node.height`, `diagram.ext_header`). A map arriving without them
  is refused rather than rendered wrong.
- **Emission order** — `sequence` on nodes, lanes, pools, flows and messages
  **is** the order elements are written into the `.bpmn` / `.drawio` file.
  Changing it changes the file produced.

BPMN identifiers (`bpmn_id`) never regenerate, and are unique **per process**,
not globally — a BPMN id only has to be unique within its own file, and a file
is one process.

## The exporters share one geometry

`generateur/geometrie.py` computes lane/pool/node positions once;
`generateur/bpmn.py` and `generateur/mxgraph.py` both consume it, and the
viewer's `rendu()` method (`models/serialisation.py`) walks the same
functions. All three outputs — `.bpmn`, `.drawio`, and the on-screen trace —
draw from a single layout pass, so there is nothing to keep in sync by hand.
`tests/test_aller_retour.py` locks this down at the level that matters:
loading a map into records, exporting it, and reimporting the export
reproduces the same nodes, flows and messages.

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

## Testing

```bash
odoo -d <db> -u bf_process --test-enable --test-tags bf_process --stop-after-init
```

`tests/test_aller_retour.py` covers load → `to_dicts()` round-trip, both
exporters' output shape, stable BPMN ids across a rename, the annotation
constraint (must be `assoc`-linked to a note), and a shared sub-process
resolving to more than one caller. `tests/test_quick_wins.py` covers the
validation register and its roll-up, the freeze (write/create/unlink all
refused, then lifted), a new version's node codes and re-exported `.bpmn`
matching the source, the validation register resetting on a new version,
version numbering skipping an already-taken number, comparison detecting a
rename and a removal, comparison reporting zero diffs between identical
copies, and — the property that actually matters — importing this module's
own BPMN export reproduces the same node count, kinds, names, flows, messages
and lane names as the source.

`qa_bf_process.py` (repo root) runs a separate, non-unittest QA pass: static
checks on the manifest/XML/ACL/RPC surface/licence/icon, plus live checks
against a running instance — constraints actually posted in PostgreSQL, views
that compile server-side, a loaded reference map's structure, and both
exports' SHA-256 matching a known-good reference.

## Notes

- The viewer (`static/src/visualiseur/`) has no editing capability by design.
  `bpmn-js` was evaluated and not adopted: its enhanced-MIT license requires a
  visible, unmasked bpmn.io watermark even in commercial/internal use, which
  is a licensing decision to make deliberately, separately from "can we see
  the map".
- Server-side PDF rendering is not implemented: it would require a PDF
  rendering library in the Odoo image, which the production image does not
  carry. A branded PDF is produced by a separate offline tool for now, from
  the same kind of level data this module stores.
