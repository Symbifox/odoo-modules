# Buildings: from the request to the work (`bf_property_operations_portal`)

An occupant files a ticket because the garage door squeaks. Somebody has to go
and fix it. Those are two objects, and this add-on is the bridge between them:
the resident's request (`bf.property.request`, from the occupant portal) and the
work order (`maintenance.request`, from building operations).

It installs by itself when both sides are present. Operations are useful
without the occupant portal (a manager who opens nothing to residents), and the
portal is useful without operations.

## What it does

| Model | Extension |
|---|---|
| `bf.property.request` | The team that answers for the building, the work orders the request produced, and a button to open one |
| `maintenance.request` | The request a work order comes from, the team and building taken from it, and the nature of the works prefilled from it |

## Routing

The request carries the building; the building names its team; so the request
reaches that team without anyone dispatching it. The team stays editable,
because a request can fall outside the ordinary. Once taken in charge, the
request is handed to the team's lead, only when nobody is already responsible
for it: a request already given to someone is not taken back. Before this,
there was one "responsible" field to fill in by hand, ticket by ticket; enough
for a twelve-door syndicate, not for a manager running janitors on shifts.

## A computed field that also carries a `default` never computes

`maintenance.request.maintenance_team_id` ships with
`default=_get_default_team_id`, which returns the first team in the database.
On create, Odoo applies that default, the field stops being "to compute", and
the compute never runs. The work order leaves for an arbitrary team while the
routing looks wired up.

This was measured on a test database, and it is invisible any other way: the
only trace is a plausible team that happens to be the wrong one. The routing is
therefore applied in `create`, before `super()`, and only where nothing was
asked for explicitly. An equipment that has its own team still decides, as in
the standard module.

## The bridge

One request may yield zero, one, or several work orders: zero when a word
settles it or when the syndicate refuses it under art. 1039 CCQ; several when
the garage door turns out to be both the motor and the rail. The request form
shows a counter of its work orders, the list can show the open ones, and
requests can be grouped by team.

**A refused request opens no work.** The button is hidden on a refused request,
and the method itself refuses: opening work on it would carry out what the
syndicate has just declined to take on as outside its object.

**A request with work under way cannot be deleted.** The link from the work
order is `ondelete="restrict"`: a request carries a thread with a person, and
erasing it under a work in progress would lose the reason for that work.

A work order takes its building from its equipment, failing which from its
request.

## What the occupant sees is not what the team sees

The occupant sees their request, its state, and what was done. They see neither
the team, nor the technician, nor the work orders, nor their durations.

This is not a display preference. The work register is closed to the portal by
access rights, and two tests enforce it rather than trusting a template: one
proves an occupant cannot search `maintenance.request` at all, the other that
they cannot read `work_ids` off a request they are otherwise entitled to read,
which is exactly the path taken by authorisation defects a security audit had
found elsewhere in the suite. The method that lists a request's work orders
also checks that the caller can read the request before answering.

## The art. 1064 CCQ reading does not cross the bridge

Who bears the cost is computed on the request, from the portion concerned and
the nature of the work. The work order does not copy it. Two places to read the
same rule is one too many, and the one that drifts is the one you will read.

## One thing does cross, and it is named: the nature of the works

The same fact serves both sides under two different rules: allocating the cost
on the request (art. 1064 CCQ), and deciding what goes up to the maintenance
log on the work order (maintenance log regulation, r. 8.01, s. 2 para. 2 subpara. 3 against s. 3
para. 2). Asking it again of whoever opens the work would make them give the
same answer twice, with the right to contradict themselves, and a regulatory
file would then carry two qualifications of the same work.

So the bridge **prefills** and imposes nothing:

- a request qualified as current maintenance gives a routine repair, a major
  repair or replacement gives a major one;
- "to be determined" is not an answer and fills nothing. It is the request's
  default, and the portal form does not ask the question: the syndicate
  qualifies. Treating it as an answer would put into the log a nature nobody
  gave;
- only corrective work orders are prefilled; a preventive one takes nothing
  from the request;
- the person on site has the last word, and requalifying the request afterwards
  does not take back what they wrote. That is the reverse of the team, which
  follows the request: a team is reassigned, a regulatory qualification is
  observed.

## Security

No access right of its own. A technician has no right on residents' requests;
reading the team or the nature of the works from the request is done under
`sudo` so that setting a team never turns into an access error on a work order
the technician is entitled to open. The request field on the work order form is
shown to property users only.

## Out of scope

Supplier work orders. A work order here is neither a quote nor a purchase
order.

## Dependencies

`bf_property_operations`, `bf_property_portal`. Installs automatically when
both are installed.

## Tested

Routing by building and its absence, the hand-off to the team lead and a
request already assigned left alone, several works from one request, team and
building inherited (and the equipment's building kept), a refused request that
opens no work, no art. 1064 CCQ reading copied, the occupant still reading
their request but never the work register nor `work_ids`, and the prefilled
nature of the works (maintenance, major, undetermined, no request, a value a
person wrote, a preventive work order).

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
