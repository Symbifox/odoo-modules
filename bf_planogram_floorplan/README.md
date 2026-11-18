# Planograms on the floor plan (`bf_planogram_floorplan`)

Where the fixture stands, on the plan of the store, and what the plan says about what is on
it.

The bridge between **Planograms** (`bf_planogram`) and **Floor plans** (`bf_floorplan`). Two
geometries meet here, and they meet in exactly one link: a floor plan is a view from above,
in centimetres, and a planogram is an elevation. Neither needs the other to be useful, which
is why the link lives in a bridge — a pharmacy shelves its goods without drawing its floor,
and an office floor plan has no shelves on it.

## What it adds

- **On the fixture**: the shape of the floor plan it stands on, and a button that opens the
  plan with that shape highlighted.
- **On the shape**: the fixtures standing on it, and a button that opens their elevation.
- **The plan then says what the elevation knows**, on the shape itself: red when a shelf is
  asked to hold more width than it has, amber when a fixture holds positions whose product
  carries no width or holds nothing at all, green when a version has been frozen — which is
  what a compliance round is counted against. Clicking a shape that carries exactly one
  planogram opens the planogram rather than the shape, because that is what you came for.
- **The tooltip of the shape** names the shelves, the positions and the capacity, and says
  when a shelf holds more than it has.
- **Seven store natures** on the floor plan itself (gondola, end cap, cooler, freezer,
  counter, checkout display, wall run) are not here: they live in `bf_floorplan`, because a
  nature added from outside is a nature its editor refuses to place, its palette does not
  offer and its drawing sizes at 50 × 50 under its technical code.

## What it does not do

- **It does not restrict a shape to one fixture.** A wall run is drawn as one shape and
  planned in sections. A fixture, on the other hand, stands in one place, and that is the
  many2one on the fixture.
- **A frozen version does not stand anywhere.** The link is not copied when a version is
  frozen: copied, every version would add a phantom fixture to the shape, and yesterday's
  overflow would colour today's plan for ever. A version keeps its origin, and the fixture
  keeps its place.
- **It reads the planogram without its rights.** The plan is a notice board: the count and
  the tooltip of a shape are read for whoever reads the plan, but only for the fixtures of
  the reader's own stores: a plan shared by two stores shows each store its own aisles. The
  fixture's own form only opens for whoever may read it, so without the right the shape
  announces no target and the click opens the shape.
- **It does not let another bridge hide the red.** The colour of a shape is a single-winner
  hook; the plan keeps the worst of what the planogram says and what the hook answered, so a
  device in good order on the same shape does not paint over an overflowing shelf.
- **It does not install itself.** Installing Planograms on a tenant that already draws floor
  plans does not switch this bridge on: it changes what every reader of a plan sees, so it is
  installed on purpose, like any other module.

## What is refused

The two must belong to the same company. A fixture of one store pinned to the floor plan of
another is a data error — it would put one store's aisle on the other's printed plan — and it
is refused by name, by whichever door it comes: the fixture's store changed, the shape moved
to the plan of another store, or the plan's store changed under its fixtures. **Archiving a
fixture is not one of those doors**: an archived fixture still stands on its shape, the three
guards see it, and standing it back up is checked again — whatever it slept with is refused
then, rather than becoming permanent. A plan whose company is empty takes a fixture of any
company, which is Odoo's own reading of an empty company.

License: Business Source License 1.1 (see LICENSE).
