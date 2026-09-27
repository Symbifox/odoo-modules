# Co-ownership: the maintenance log and operations (`bf_property_operations_records`)

A boiler has two records, and neither contains the other.

The **regulatory reading** lives on the maintenance log of
`bf_property_records`, the one an independent professional establishes under
the regulation (CQLR, c. CCQ, r. 8.01): installation date, required servicing
and its stated frequency, routine repairs, contracts, inspection reports,
manufacturer manuals, estimated condition, remaining useful life, major work
with its year and cost.

The **operational reading** lives on `maintenance.equipment` in
`bf_property_operations`: category, vendor, model, serial number, cost, warranty
expiry, team, technician, MTBF, MTTR, last failure.

This bridge lets the log **cite** the equipment, signals when the two diverge,
and carries back to the log three dated facts that only operations observes.

## What it adds

| Where | What |
|---|---|
| Log item (`bf.property.maintenance.item`) | The cited equipment (restricted to equipment of the item's building; choosing it fills an empty building), a drift signal (scrapped or archived in operations), the count of scheduled preventive work declared not done on that equipment, and a summary with one line per skipped occurrence (date, work order, reason). |
| Maintenance log (`bf.property.maintenance.log`) | Two warning banners at the top of the log: items whose equipment operations has taken out of service, and items carrying scheduled preventive work declared not done. |
| `maintenance.equipment` | A "Maintenance log" tab listing the log items that cite it, shown to the suite's Consultation group only. |
| `maintenance.request` | The three write-backs described below. |

## The failure mode is drift, not duplication

Neither reading is redundant, so merging them would destroy information. What
costs money is the day the log still describes a boiler that operations has
already scrapped, and the syndicate files a regulatory document that lies by
omission.

So the log item cites the equipment and raises a signal when the equipment is
scrapped or archived. It merges nothing and copies no descriptive field from
one side to the other.

## The citation points one way

The log points at operations, not the reverse. A log is a dated document that an
independent professional establishes, and once established it cites the state of
the estate at that moment. Operations keeps living: it replaces, it scraps, it
buys again.

The citation is `ondelete="restrict"`: deleting an equipment record would
otherwise erase a trace from a regulatory document without anyone learning of
it. The normal way out of service is scrapping or archiving, and those are
signalled. A log item and its cited equipment must be in the same building.

## The signal does not block

Article 4 of the regulation requires the annual update to state what was not
done **and why**. That reason comes from a person, never from a computation. A
signal that refused the save would stop the log from telling the truth, which is
the opposite of what the regulation asks.

The drift signal clears itself when the board does its work: recording
completed work on the log item turns it off.

It is shown at the top of the log, not only in an optional column. A warning
sitting in a two-hundred-row list reaches only someone already looking for it.

## What operations reports back to the log

Three writes are the exception to "nothing is copied", and they are not copies
of fields: they are three **dated facts** the regulation puts in the log and
that only operations observes.

- **Required servicing done** (art. 2 para. 2 subpara. 2): when a scheduled
  preventive work order is closed, its closing date becomes the item's last
  maintenance date.
- **Routine repair done** (art. 2 para. 2 subpara. 3): when a corrective work
  order qualified as a *routine* repair is closed, its closing date becomes the
  item's last repair date. A corrective order whose nature nobody has stated
  reports nothing, and a major repair or replacement (art. 3 para. 2, which also
  wants its cost) is not written here.
- **Scheduled work not done** (art. 4): when a scheduled preventive work order
  is declared not done, its date, name and reason are written to the item's
  "not done" reason.

Rules that hold for all three:

- **Only an established log is updated.** A replaced log is a dated historical
  document, and writing to it today would falsify what it said at its date. A
  draft is not yet a log: the professional composes it.
- **A date never moves backwards.** Work closed late does not overwrite a more
  recent date already in the log.
- **A reason written by a person is not overwritten.** If the board has already
  written a reason, the operational one is not written over it; the occurrence
  still goes to the log's thread, and the item's summary lists every skipped
  occurrence, including those a one-line field cannot hold.
- **Every write is stated on the log's thread**, as an internal note authored by
  the person who closed the work, naming the item, the value written (and the
  value it replaces) and the work order. The bridge writes under `sudo()` on
  behalf of someone who has no right on the log, so the thread is where an
  auditor can read it. The log item does not become a thread of its own: two
  hundred items would make two hundred threads nobody opens.

## Security

A caretaker closing a work order has no read access to the maintenance log,
which lives behind the co-ownership groups. The lookups and writes into the log
therefore run in `sudo()`; they only touch items that cite the equipment of the
work being closed, and each write is traced on the log's thread. The skipped-work
summary on a log item is also computed in `sudo()`, so a co-ownership manager
who may read the log is not refused on work orders they cannot read; it shows
only the work of the equipment that item already cites.

## It installs itself

An auto-installing bridge. Operations is worth having without the log (that is
the rental-building manager's case), and the log is worth having without
operations. The bridge appears only when both sides are present.

## Dependencies

`bf_property_operations`, `bf_property_records`. Auto-install.

## Tests

45 tests: citation and building coherence, a cited equipment that cannot be
deleted, drift on scrapping and archiving, its clearing by completed work and
the count on the log, a drift that never blocks the save, the preventive and routine
repair write-backs (established log only, no backwards date, unqualified or
major repairs ignored, thread note with its author), and the not-done reason
(written where the log was silent, never over a person's text, every occurrence
listed).

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose. A syndicate, a housing cooperative
  or a non-profit housing organisation running the module for its own immovable
  is covered, and so is the bookkeeper or the manager it hires who works inside
  its instance.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-30, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
