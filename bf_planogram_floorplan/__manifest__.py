# -*- coding: utf-8 -*-
{
    "name": "Planograms on the floor plan",
    "summary": "Where the fixture stands, on the plan of the store, and what the plan says "
               "about what is on it",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # "Other proprietary" is the value Odoo's manifest schema offers to say BUSL-1.1: it has
    # no BUSL value. The LICENSE file governs, not this line.
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # No "auto_install" (a deliberate choice). The bridge is installed on purpose, never by
    # the side effect of installing Planograms: it changes what a floor plan shows to
    # whoever reads it, on tenants that already run floor plans, so someone decides it.
    "description": """
Planograms on the floor plan
============================

The bridge between **Planograms** and **Floor plans**. Two geometries meet here, and they
meet in exactly one link.

A floor plan is a view from above, in centimetres: it says the gondola stands at the end of
aisle 3, against the wall. A planogram is an **elevation**: it says what is on that gondola,
shelf by shelf, facing by facing. Neither needs the other to be useful, which is why the
link lives in a bridge: a pharmacy can shelve its goods without drawing its floor, and an
office floor plan has no shelves on it.

What the bridge adds
--------------------
* On the fixture: the element of the floor plan it stands on, and a button that opens the
  plan with the fixture highlighted.
* On the element of the plan: the fixtures standing on it, and a button that opens their
  elevation.
* The plan then says, on the shape itself, what the elevation knows: red when a shelf is
  asked to hold more width than it has, amber when a fixture holds positions whose product
  carries no width, or holds nothing at all, green when a version has been frozen, which is
  what a compliance round is counted against. Clicking a shape that carries exactly one
  planogram opens the planogram rather than the shape, because that is what you came for.
* The tooltip of the shape names the shelves, the positions and the capacity.

One element can carry several fixtures (a wall run split into sections), and a fixture
stands in one place: that is the many2one on the fixture, and there is no unique constraint
on the element.

The two must belong to the same company. A fixture of one store pinned to the floor plan of
another is a data error, and it is refused by name.
""",
    "depends": ["bf_planogram", "bf_floorplan"],
    "data": [
        "views/bf_planogram_fixture_views.xml",
        "views/bf_floorplan_element_views.xml",
    ],
}
