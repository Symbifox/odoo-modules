# Floor Plans (`bf_floorplan`)

The plan of an office floor stops being an image filed away somewhere and
becomes a reference: every room, every desk and every device placed on it is
an Odoo record, with its own form, its own discussion thread and its photos.
The architect's drawing stays what it is, a background image. What lives on
top of it is data.

Odoo 18.0 Community. Licensed under **BUSL-1.1** (see `LICENSE`): free for
your own internal use, reverting to LGPL-3 four years after each release.

## What it does

* **One plan per floor**, in real-world centimetres, on an optional PNG or
  JPEG background. The image is stretched to the plan's size: give the plan
  the real width and depth of what the picture shows, and the grid is to
  scale.
* **Zones**: private office, meeting room, open space, technical room,
  storage, reception, kitchen, restrooms, circulation. Each has a capacity,
  and its occupancy is computed from the desks in it that have someone
  assigned.
* **Elements**: desk, table, printer, wall screen, network switch, Wi-Fi
  access point, server, rack, network outlet, phone, camera. An element knows
  which zone it is in without being told (the zone containing its centre, the
  smallest one if several overlap) and who sits there (`hr.employee`).
* **Links** between elements (copper, fibre, power, other): the beginning of
  a cabling diagram, drawn on the same page as the plan.
* **A built-in editor**, in SVG, with no third-party library. Six tools:
  view, move (with a resize handle), place, rotate, link, remove. Every gesture
  is an ORM write, after which the server sends the whole plan back: the
  browser computes nothing.
* **A frozen plan** cannot be edited until it is reopened. Duplicating a plan
  carries its zones, elements and links, rebuilt on the copies.
* **Outputs**: PDF (letter landscape, with legend and a per-zone inventory),
  SVG from the screen (background embedded), and **diagrams.net** (`.drawio`)
  using the shapes of its "Floorplans" library and one layer per kind, so the
  drawing can be finished (walls, doors, windows) where that is best done.
* **Find someone or something**: a search box above the plan highlights every
  zone or element whose label, occupant, kind, zone or linked record matches.
  Escape clears the search, drops a half-done gesture, or leaves full screen.
* From an employee's form, "On the plan" jumps to their desk, highlighted; from a
  zone's form, "On the plan" highlights the room. Plan cards show a thumbnail
  of the background.
  The button is also on the public employee profile, so people without HR
  rights can find a colleague too.

## Access rights

Every internal user can read plans: a plan is first of all how you find
someone or something. The **Floor Plans / Manager** group draws. A company
rule applies on all four models.

## For bridge modules

Three hooks on `bf.floorplan.element`, meant to be overridden:

* `_cible()`: `False`, or `{"modele", "id", "nom"}`. Clicking the shape opens
  the target instead of the element.
* `_teinte()`: `""`, `"alerte"`, `"attention"` or `"ok"`. The shape's outline
  says it.
* `_infos()`: the sentences of the tooltip.

`bf_floorplan_hosting` is the worked example: endpoints and servers from the
hosting management module, placed on the plan.

## Known limits

* Walls, doors and windows are not drawn here: they come from the background,
  or get finished in diagrams.net after export. The module carries what has a
  record, not the masonry.
* The background is a raster image. Convert an architect's SVG to PNG before
  uploading it: serving a third-party SVG as-is would serve its scripts too.
* Zones and elements are rectangles, rotated by quarter turns. An L-shaped
  room is two zones.

## Changelog

* **18.0.1.1.0** — search box with highlighting, Escape key, zone highlight
  from its form, background thumbnails on plan cards, background copied with
  the plan, the day's diagrams.net export replaces the previous one instead
  of piling up, unique names when placing shapes, readable errors on
  malformed coordinates or identifiers.
* **18.0.1.0.2** — copy of a plan tolerates archived elements; export
  available to readers; link kind validated.
* **18.0.1.0.1** — "On the plan" also on the public employee profile.
* **18.0.1.0.0** — first release.

## Language

The user interface is in French (labels, kinds, messages). The module ships no
`i18n/` directory yet: every string is translatable, and translations are
welcome.

## Demo data

A 18 m × 12 m floor: seven zones, eight desks in the open space, a cabled
technical room, and a background drawn for the occasion.
