# Buildings: Resident Access (`bf_property_access`)

A building's front-door intercom, its parcel lockers and its card readers each
keep their own list of who may come in. Nobody keeps those lists in step with
who actually lives in the building: a new tenant waits for the caretaker to add
them, and a former owner's fob still opens the garage two years later. This
module makes the property file the source of truth for **who has a right to
which door**, and turns every arrival and every confirmed departure into an
order: add this person to that device, or remove them.

It does not replace the intercom, the locker or the lock. It tells them whom to
add and whom to remove.

## What it adds

| Where | What |
|---|---|
| `bf.property.access.point` | A door, intercom, garage, parcel locker, elevator, common room or **unit door**, in a building, optionally tied to one private unit. Its credential (directory entry, PIN, card or fob, mobile credential, physical key), its connector, and the person responsible for doing orders by hand. |
| `bf.property.access.resident` | A person who has a right to enter: who, which building, which unit (empty for a room let without a registered unit), where the right comes from, arrival date, the date the keys were handed back, and the departure signal read from the file. States: upcoming, active, departure to confirm, left, cancelled (never arrived). |
| `bf.property.access.grant` | One right: a resident at one access point, with its credential and its external reference (card number, directory id). **No PIN or secret is ever stored.** Unit doors carry the landlord's and the tenant's consent, dated, with who recorded them. A lost or stolen credential is removed alone and reissued. |
| `bf.property.access.order` | The queue: add or remove, to send, to do by hand, done, failed, cancelled. Each order has its attempts, last message, who did it and when. |
| `bf.property.access.connector` | How an order reaches a device. This version ships the **manual** connector only: the order becomes an activity for the access point's responsible person ("Add Alice Example (unit 101) to North intercom: directory entry"). Marking the activity done marks the order done, and the reverse. A hardware connector module adds a `kind` and a `_push_<kind>(order)` method; after `max_attempts` failures the order falls back to the responsible person. |
| `bf.property.building` | A responsible person for access (receives "departure to confirm" activities) and the list of its access points. |
| `bf.property.organisation` | How long the access journal is kept (days, default 90); whether residents may give their announced visitors temporary access, and its maximum length (12 hours by default). |
| `bf.property.access.pass` | A temporary access: visitor, contractor, staff or entry into a dwelling, its holder, access points, credential and window, and for an entry its reason, notice, the tenant's agreement or the emergency. |

## Temporary access (visitors, contractors, entry into a dwelling)

A temporary access gives a person who is neither a tenant nor an occupant a set
of access points for a time window: a visitor, a contractor for the afternoon,
staff, or an entry into a dwelling. On confirmation, an add order leaves for
each point, carrying the window (for a connector that can set it); when the
window ends, a quarter-hourly run orders the removal. Removing at a fixed time
is lawful here: the holder is not protected by a lease.

**Entry into a dwelling (arts. 1930 to 1933 CCQ).** The reason is recorded
(checking the condition of the dwelling, works, a showing to a prospective
buyer or lessee, emergency). Outside an emergency, a 24-hour notice is required
and its date is recorded; the tenant may refuse a check or a showing before
9 a.m. or after 9 p.m., and works before 7 a.m. or after 7 p.m. The module will
not confirm an entry outside those bounds unless the tenant's agreement is
recorded; an emergency needs a written reason. An entry is not a lock change:
the unit door is given without the two consents of art. 1934.

A grant now carries either a resident or a temporary access, never both. A
lost PIN is removed alone and reissued, as for residents.

## Departure is never inferred from a date

A dwelling lease renews by operation of law (art. 1941 CCQ), selling the
building does not end it (art. 1937), only the tribunal can remove a tenant who
stays (art. 1889), and cutting someone's access to push them out may be
harassment, with punitive damages (art. 1902). So nothing here removes access
because a date has passed.

Each night (and from the "Sync with the file" button) the module asks the file
who occupies each unit: co-owners living in a unit that is not rented, the
occupant recorded on a rented unit, and, with `bf_rental_access`, the
signatories of the lease. A resident drawn from the file whose person, building
and unit **no source produces any more** moves to *departure to confirm*, with
the reason in plain words and an activity for the building's access manager.
Their access stays in place. It is removed only once someone enters the date
the keys were handed back, which cannot be in the future.

*It's not a departure* turns the resident into a **declared occupant**: a
person now vouches for them, and the nightly run stops flagging them (a co-owner
who sold and stayed on as the new owner's tenant is the usual case).

The reverse holds too: **a confirmed departure stays confirmed**. If the file
still names the person afterwards (a co-lessee who moved out but is still on
the lease, an occupant still in the register), nothing is reissued; the
manager gets one "update the file" activity, and a return is declared by hand.
Only a source that starts after the keys were handed back (a new lease) makes
a new resident. The record that carries this guard is not purged while the
file still names the person.

Each building is synchronised on its own: one inconsistent record is logged
and never stops the others.

## People who signed nothing

A spouse, a civil union or de facto partner, a parent or relative who has lived
in the dwelling for six months may remain in it (art. 1938). They are declared
by hand as occupants of the unit. The departure of the person who signed does
not touch their access.

## Unit doors need two consents

No lock or other device restricting access to a dwelling may be installed or
changed without the consent of both landlord and tenant (art. 1934, public
order by art. 1893). A grant on a unit door waits in *awaiting consents* until
both have been recorded, and no order leaves before that.

## The lock never depends on Odoo

Devices validate credentials themselves. Odoo only sends add and remove orders,
late if it must, and replayable. An Odoo outage delays an addition or a removal;
it never keeps a door shut. Fire-safety behaviour of magnetic locks (release on
alarm and power failure, CNB-Q 3.4.6.16) belongs to the hardware and is never
touched by software.

## Privacy (Québec Law 25)

- The journal (residents who left, removed grants, closed orders) is deleted
  after the organisation's retention period (art. 23 of the private-sector Act);
  0 means never and has to be justified in the privacy impact assessment.
- No biometrics: identity verification by a biometric characteristic needs a
  prior declaration to the Commission d'accès à l'information and express
  consent, and the Commission has ordered facial recognition stopped where less
  intrusive means existed (Act respecting a legal framework for information
  technology, ss. 44-45).
- A tenant who refuses a mobile credential is entitled to a card or a key.

## Security: who sees what

- **Consultation** reads points, residents, grants, orders and connectors.
- **Manager** creates access points and connectors, declares occupants,
  confirms or dismisses departures, records consents, reports lost credentials.
- The **responsible person of an access point** must hold Consultation (a
  constraint checks it): they receive the order activities, mark them done, and
  may mark an order of *their* point done; nobody else below manager may. The
  building's **access manager** must be a Manager, since they confirm departures.
- **Only the activity the module handed over counts.** Odoo lets the assignee
  rewrite an activity (model, record, type), so the hook never trusts the
  activity's target: the order keeps the id of the activity it handed over, and
  marking *that* activity done completes the order only if the user is a
  manager, or both its assignee and the point's responsible person. Otherwise
  the order is handed back. A deleted reminder is handed over again at night,
  and reminders follow a change of responsible person.
- Grants and orders are created by the module, never by hand. States, signals,
  consents, origins and the order journal are written only by the module's own
  code (`sudo`), never because of a context key, which any RPC client can set.
  Only an order's reference is written by hand, until the order is closed.
- Changing an access point's scope or archiving it orders the removals it
  implies; deleting a point that still has rights is refused.
- Multi-company rules on every model.

## Dependencies

`bf_property_core`, `mail`.

## Tests

55 tests: sources and arrivals, scoped points, idempotent sync, unit-door
consents and their reminder, departure flagged and never removed, confirmation,
declared occupants spared, dismissal, a confirmed departure that stays
confirmed (next day, after purge, sources without a real date), signal lifted
when the file is corrected, cancelled arrivals, revocation of an order already
handed over, rewritten or reassigned activities, deleted reminders, role checks,
fields that a context key does not unlock, lost credentials, connector retries,
failures and exceptions, retention that never deletes pending work,
per-building sync, multi-company isolation, every view for both roles. Two
adversarial reviews; mutations of the key rules are caught by the tests.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-11-01, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
