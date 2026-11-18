# -*- coding: utf-8 -*-
{
    "name": "Planograms",
    "summary": "The shelf drawn from the records: fixtures, shelves, facings, capacity, "
               "and a version frozen for the round that counts it",
    "version": "18.0.1.0.2",
    "category": "Inventory/Inventory",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # "Other proprietary" is the value Odoo's manifest schema offers to say BUSL-1.1: it has
    # no BUSL value. The LICENSE file governs, not this line.
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "description": """
Planograms
==========

A planogram is an **elevation**, not a floor plan: x runs along the fixture, y rises from
the floor. A floor plan says where the gondola stands; this says what sits on it, shelf by
shelf, facing by facing, and what that adds up to.

It does not depend on the retail suite, and that is deliberate: a pharmacy, a hardware
store, a library shelves goods without following a cost or a margin.

Features
--------
* Fixtures to scale, in centimetres: gondola, end cap, cooler, freezer, counter, checkout
  display, wall run. A fixture posed from the list already carries the ordinary dimensions
  of its kind.
* Shelves with their height from the floor, their usable width and depth, their tilt and
  the load they are rated for. The list reads from the top down, the way the fixture is
  read standing in front of it.
* Positions: a product, its facings, how many stand behind each facing and how many are
  piled on top. Capacity is the three multiplied, and it is what days of supply at the
  shelf waits for.
* The width taken is read from the **product**, never typed on the position: six dimension
  fields (the selling unit and the case), which Odoo itself does not carry.
* What a shelf has left, in centimetres and in percent, and the linear shelf of the whole
  fixture, which is the denominator of a space to sales ratio.
* A product with no width is never silently treated as fitting: the position is counted as
  unmeasured, on the shelf, on the fixture, in the drawing and in the printed plan.
* The elevation drawn in SVG from the records, on screen and on paper, with what does not
  fit flagged: past the end of the shelf, taller than the gap above it, drawn at a nominal
  width for want of a measurement.
* A version frozen with its shelves and its positions. The fixture stays editable; what is
  published is the version, and a version never changes again. That is what a compliance
  round is counted against, and a round counted against a drawing that moved under it
  measures nothing.

What it does not do
-------------------
* No pixel editor, and no 3D. A shelf is a comb, not a canvas: a position holds to its
  shelf, to the width it takes and to the width left. The constraint is the feature, and it
  is what makes the capacity computable.
* Nothing is refused for not fitting. A planogram is built by moving things around, and an
  editor that refused the intermediate state would force the aisle to be emptied before it
  could be filled. What does not fit is counted and drawn, never hidden.
* Depth and stacking are trusted as typed: only the WIDTH is checked against the shelf,
  because the width is what the capacity and the linear shelf are made of.
* Sales by position, space to sales, space to margin and GMROI are not here: they need a
  sales history, and they live in the bridge to the retail suite.

What it writes in the chatter
-----------------------------
Freezing a version leaves a **log** on the fixture: it stays on the record, it sends no
email and raises no bell. And the body comes out in the language of the company the fixture
belongs to, not in the language of whoever froze it, because what the chatter keeps is read
later, by someone else.
""",
    "depends": ["product", "mail", "web"],
    "data": [
        "security/bf_planogram_security.xml",
        "security/ir.model.access.csv",
        "views/bf_planogram_fixture_views.xml",
        "views/bf_planogram_shelf_views.xml",
        "views/bf_planogram_position_views.xml",
        "views/product_views.xml",
        "report/bf_planogram_report.xml",
        "views/bf_planogram_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_planogram/static/src/elevation/elevation.js",
            "bf_planogram/static/src/elevation/elevation.xml",
            "bf_planogram/static/src/elevation/elevation.scss",
        ],
    },
}
