# Planograms (`bf_planogram`)

The shelf drawn from the records: fixtures, shelves, facings, capacity, and a version frozen
for the round that counts it.

A planogram is an **elevation**, not a floor plan: x runs along the fixture, y rises from the
floor. A floor plan says where the gondola stands; this says what sits on it, shelf by shelf,
facing by facing, and what that adds up to. Neither Odoo nor a spreadsheet can say how many
units a shelf holds, because nothing in either of them knows how wide a can of beans is.

It does not depend on the retail suite, and that is deliberate: a pharmacy, a hardware store
or a library shelves goods without following a cost or a margin.

## What it adds

- **Fixtures to scale, in centimetres.** Gondola, end cap, cooler, freezer, counter,
  checkout display, wall run. A fixture posed from the list already carries the ordinary
  dimensions of its kind, so nobody types 183 the first time. Picking a kind fills the
  dimensions that are still **blank** and never touches a measurement typed by hand: a
  measured fixture is the one thing the module could not recover.
- **Shelves**, with their height from the floor, their usable width and depth, their tilt
  (a bread rack leans towards the aisle) and the load they are rated for. The list reads
  from the top down, the way the fixture is read standing in front of it.
- **Positions**: a product, its **facings** (what the aisle sees, side by side), how many
  stand **behind** each facing, and how many are **stacked** on top. Capacity is the three
  multiplied, and it is the number the rest of the suite waits for: days of supply at the
  shelf is capacity over daily velocity, and neither is computable without a position.
- **Six dimension fields on the product**, which Odoo does not carry: the selling unit and
  the case, in centimetres. Odoo 18 knows a product's `volume` and its `weight`, and neither
  says how wide the box is. The width a facing takes is read from the product, never typed
  on the position.
- **What a shelf has left**, in centimetres and in percent, and the **linear shelf** of the
  whole fixture: the usable width of every shelf added up. That is the denominator of a
  space to sales ratio, and the reason a shelf carries a usable width of its own.
- **A shelf wider than its fixture is flagged, not refused.** A usable width of 1220 where
  122 cm was meant multiplies the linear shelf by ten, and the space to sales ratio with it.
  The shelf is counted as typed and **drawn whole** — the drawing grows rather than cropping
  what it cannot hold — and the shelf, the fixture and the printed plan all say that it is
  wider than the fixture.
- **A product with no width is counted, never assumed to fit.** It takes zero centimetre,
  which would quietly report a full shelf as half empty, so the position is counted as
  unmeasured on the shelf, on the fixture, in the drawing and on the printed plan. Products
  not measured for the shelf are a filter of their own in the catalogue.
- **The elevation drawn in SVG from the records**, on screen and on paper, from one geometry:
  the client recalculates nothing. What does not fit is flagged rather than hidden: past the
  end of the shelf, taller than the gap to the shelf above, or drawn at a nominal width for
  want of a measurement.
- **A version frozen** with its shelves and its positions. The fixture stays editable; what
  is published is the version, and a version never changes again, not its dimensions, not
  its shelves, not its positions — not even when the product sheet is corrected afterwards.
  What the freeze copies onto every position, and what the plan is reprinted from ever
  after, is named here: the **width** and the **height** the product measured that day, its
  **name** and its **reference**. Rename a product, recode it, correct its dimensions,
  archive it: the frozen plan still says what stood on that shelf. That is what a compliance
  round is counted against, and a round counted against a drawing that moved under it
  measures nothing. What the freeze does *not* copy is the product record itself — a
  position still points at it, which is why a product on a shelf cannot be deleted.
- **And a version does not disappear either.** Deleting the living fixture leaves its frozen
  versions standing, each with its number, its date and whoever froze it; only the link back
  to the deleted fixture is emptied. A frozen version itself is never deleted, only archived.

## What it does not do

- **No pixel editor, and no 3D.** A shelf is a comb, not a canvas: a position holds to its
  shelf, to the width it takes and to the width left. A position has no free coordinates at
  all — its place is its order and the widths before it — so moving a product is reordering a
  list. The constraint is the feature: it is what makes the width left, and therefore the
  capacity, computable.
- **Nothing is refused for not fitting.** A planogram is built by moving things around, and
  an editor that refused the intermediate state would force the aisle to be emptied before
  it could be filled. What does not fit is counted and drawn.
- **Depth and stacking are trusted as typed.** Only the width is checked against the shelf,
  because the width is what the capacity and the linear shelf are made of. A product too
  deep for its shelf is drawn, not refused.
- **No sales, no ratios.** Sales by position, space to sales, space to margin, GMROI and
  days of supply need a sales history: they belong to the bridge to the retail suite, not
  here.
- **No floor plan.** Where the fixture stands is the other geometry, and the module
  « Planograms on the floor plan » (`bf_planogram_floorplan`) carries that link. This one
  installs without `hr` and without office floor plans.

## What it writes in the chatter

Freezing a version leaves a **log** on the fixture: it stays on the record, it sends no email
and raises no bell. A manager without an email address on their user is an ordinary thing in
a store, and a notification would have made the freeze itself fail for them.

And the body comes out in the **language of the company** the fixture belongs to, not in the
language of whoever froze it. What the chatter keeps is read later, by someone else, and the
language of the reader is the store's.

## Groups

- *Shelf reader*: reads the plan and prints it. No change to the drawing: a facing moved by
  the person restocking, without the plan being redrawn, is how a planogram stops describing
  the aisle.
- *Planogram manager*: draws the fixtures, the shelves and the positions, and freezes the
  versions. Reads and **writes** products and product templates, without creating and
  without deleting, because typing the six dimensions is their job and making or removing an
  article is not. No group of Odoo's own is implied, so the role carries no menu nobody asked
  for.

License: Business Source License 1.1 (see LICENSE).
