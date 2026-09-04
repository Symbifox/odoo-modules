# Échéancier (`bf_gantt`)

Odoo Community has no Gantt view, and no start date on tasks. This module adds
both, with **no Gantt library**: the drawing engine is its own. It does use the
Python libraries a standard Odoo image already ships (see Requirements).

## One geometry, several outputs

Everything this module shows, prints or exports comes from the same place.

```
bf.gantt.source          project.project  or  bf.gantt.plan
        │                        │
        └──────────► normalised payload (same keys in both cases)
                             │
                    generateur/geometrie.py     ← coordinates, computed ONCE
                             │
     ┌────────┬──────────┬───┴────┬─────────┬──────────────┐
    PDF      PNG        SVG     XLSX      MSPDI      OWL component
 reportlab  Pillow   hand-written xlsxwriter lxml   (receives the geometry
  + Lexend                                            over RPC, computes nothing)
```

The browser component **computes no position of its own**: it asks the server
for the same geometry the PDF uses. That is what guarantees that what you see on
screen is what comes out of the printer. The price is one round trip when you
change the time scale; the client caches them.

## What it does

| | |
|---|---|
| **A real start date** | `planned_date_begin` on `project.task`, deliberately the same field name Enterprise uses, so nothing is lost if a database later moves. Left empty, the display falls back to the assignment date, then to the creation date, **and says so**: the bar carries a dashed edge and the tooltip names the approximation. |
| **Two sources** | a project's schedule, or a standalone `bf.gantt.plan` that creates no task at all (for a quote, an implementation plan). |
| **Grouping** | stage, milestone, assignee, company, none. Progression step is offered **if** `bf_stepbystep_clients` is installed: offered, never required. |
| **Portal, no seat** | a token address per project or per plan, read-only, for people who have no account. For a customer who *does* have a portal account, a button on their project page and a card in `/my`, so they find it without being sent a link. |
| **Display size** | the drawing is calibrated for print, so one PDF point is one screen pixel, which is too dense to read. A zoom stretches the drawing's box without touching the coordinate system, so it stays sharp at any factor. The UI offers 100 % to 250 %; a hand-written portal address is accepted anywhere from 60 % to 300 % and clamped outside that. It follows the screen, the PNG and the SVG; never the PDF. |
| **Branded outputs** | PDF, PNG, SVG and XLSX, drawn server-side, carrying the company **logo**, **colours** and tagline read from `res.company`. Nothing to configure: a database with no branding module still gets a document in its own colours. |
| **Bounded work** | the span is capped at ten years and the axis at 2000 ticks, so one mistyped deadline cannot turn a public route into a resource sink. Nothing disappears quietly: a bar that runs past the window is **pinned at the edge with a chevron**, and every output says in its footer that the span was clamped. |
| **File exchange** | import and export of MSPDI (Microsoft Project's `.xml`, which is also what OpenProject produces), and import/export of XLSX. |

## What it does not do, and why

- **No critical path, no automatic scheduling, no resource levelling.** These are
  the features commercial Gantt libraries keep for their paid editions, nobody
  asks for them on the app store, and they are expensive to get right.
- **No `.mpp`.** Microsoft Project's binary format can only be read with MPXJ,
  which is written in Java and would require a Java virtual machine in the Odoo
  image. Project can export MSPDI: `Save As`, then `XML`.
- **No drag and drop.** The schedule reads, it does not write. Dates are changed
  on the task or on the plan line.

## The portal's three guards

1. the **token**, checked by Odoo's own `_document_check_access`;
2. the **published flag**: a token that exists is not permission to publish, and
   you must be able to close a schedule again without changing its address;
3. **the same check on the files as on the page**, otherwise guessing the PDF's
   address would be enough to walk around the rest.

Copying the private address is guarded by the same right as publishing, because the
call mints the token on its way past. Publishing itself is guarded **on the server**,
not in the view: `write()` and
`create()` refuse to set the published flag without the manage group, on both
`project.project` and `bf.gantt.plan`, so write access on the project alone is not
enough to open a schedule to the internet. The plan's ACL says the same thing a
second time; the code guard is there so loosening the ACL later cannot silently
open publishing.

For a signed-in portal user the record rules do the work instead of the token,
and they scope to the **commercial partner**, not to the contact: a customer
whose three staff each have an account sees the same schedule from all three.

⚠️ A published page shows **task names and assignees**. Read it before handing it
to a wide audience.

## Groups

`Read` and `Manage and publish`. ⚠️ `Read` **implies** Odoo's own `Project / User`
group, since there is nothing to schedule without it: granting it promotes the
user to Project User. ⚠️ **Neither is granted to any existing profile
on install**, including on a fresh one: publishing makes a schedule readable
without an account, and that is a permission you give to someone, not one that is
inherited from a project role. After installing, **grant the groups by hand** or
the application stays invisible.

## Logos

The company logo is read from `res.company` and drawn top right. A **raster**
logo (PNG, JPEG) appears in every output. An **SVG** logo is embedded as-is in
the SVG output, where it is sharp at any size; the raster outputs fall back to
the company name set in the brand colour, because rasterising SVG would require
`cairosvg` or `svglib` and this module adds nothing to the Odoo image. If you
want the real logo everywhere, upload a PNG on the company.

## Requirements

`project`, `hr_timesheet`, `portal`. On the Python side: `reportlab`,
`xlsxwriter`, `openpyxl`, `lxml`, `Pillow` — **all already present in a standard
Odoo image**. Nothing to add.

Lexend is bundled in `static/fonts/` under the **OFL 1.1** licence (the licence
text sits next to the files), because a drawing that changes typeface changes
its text widths.

## Tests

`--test-tags bf_gantt`:

- `test_geometrie`: placement, outside any database (no cursor is opened).
- `test_echange`: XLSX and MSPDI round trips, SVG escaping, external XML entities
  left unresolved — the tests capture the parser **the module itself passes** to
  lxml, so removing `resolve_entities=False` makes them fail — recursive-entity
  bombs refused, upload size and row caps, clamped-span notices, logo handling
  including the SVG case.
- `test_source`: against a real database, every record created on the spot.
- `test_portail`: real HTTP requests, refusals as well as successes.

⚠️ It is the configuration's `dbfilter` that decides which database answers HTTP
requests, **not** the `-d` on the command line. On a test server that sets one,
pass `--db-filter=^<test database>$` or the portal tests hit the wrong database
and return a 404 that looks like a badly written route.

⛔ The OWL component has **no** automated test: it needs a browser engine. What is
proven is that its files enter the asset bundle and that the stylesheet compiles.

## Changelog

| Version | Notes |
|---|---|
| 18.0.1.5.3 | Copying a schedule's private address now needs the same right as publishing one. The method is callable over RPC and mints the token as it goes, so hiding its button was never enough: anyone who could read the project could mint a link and hand it out. |
| 18.0.1.5.2 | Field labels that were falling back to their technical English names now read properly in the interface. |
| 18.0.1.5.1 | Several assignees on one task read as `Jane D. +2` instead of two full names side by side, which overflowed the label column and ran under the task name. Shortened at the source, so all five outputs agree. |
| 18.0.1.5.0 | The header band is painted rather than left transparent, and the task name and assignee each get their own column, so a long name can no longer run into the one beside it. |
| 18.0.1.4.4 | 🔴 The backend view could not open: the geometry sent to the browser carried the company logo's raw bytes, which JSON cannot serialise, so the RPC call died and the component reported a connection error. The browser does not draw the logo, so it is no longer sent. A test now asserts the payload survives `json.dumps`. |
| 18.0.1.4.2 | The origin-of-start field moves out of the main task pane, where it cluttered every task without a planned start, and sits beside the assignment date in developer mode. The schedule and the portal still state it themselves. |
| 18.0.1.4.1 | A bar running past the clamped window is pinned at the edge with a chevron instead of being drawn off-canvas, and every output states the clamp. Colour validation moved to a single place; two remaining date-overflow sites closed; the public routes refuse cleanly instead of returning 500. |
| 18.0.1.4.0 | Publishing is guarded server-side, not only in the view. The schedule span is capped, so a mistyped deadline can no longer turn a public route into a resource sink. Upload size and row caps on imported workbooks; escaping hardened on the import preview and on SVG attributes. |
| 18.0.1.3.2 | Header band grows to fit the logo, so the reading marker no longer collides with the "today" label. |
| 18.0.1.3.1 | Larger logo in the header. |
| 18.0.1.3.0 | SVG logos recognised and measured; embedded as-is in the SVG output, wordmark fallback in the raster outputs. |
| 18.0.1.2.0 | Customer portal: button on the project page, `/my` card, `/my/echeanciers` list, portal ACL and record rules. Company logo, colours and tagline in every output. |
| 18.0.1.1.0 | Adjustable display size, 100 % to 250 %, remembered per browser and carried in the portal address. |
| 18.0.1.0.0 | First release. |

## Licence

BUSL-1.1, Change Date **2030-09-04**, Change Licence LGPL-3.0-or-later. Internal
use stays free; reselling or hosting it for a third party needs a written
agreement. See `LICENSE`.
