# Work Routes: Privacy Bridge (Quebec Law 25)

> **Not legal advice.** The retention term and the legal basis posed here are a starting
> point for Quebec's *Act respecting the protection of personal information in the private
> sector*. Review them with the person responsible for privacy in your organisation.

## What was found

`bf_route` reads the position of the person on the road once, at the moment they mark a
stop, and only when the company turned the setting on and the person has read the notice.
The guard rail was already there: a versioned notice, an acknowledgement that keeps the text
as it was read, no continuous tracking, erasure by retention.

What was missing was the trace in the register. Measured on a real base: dozens of declared
purposes, dozens of rules in the retention calendar, and none of them spoke of the position
of a person on the road. This module aside, 16 other applications carry a privacy bridge,
and this one carried none.

## What this bridge poses

**Three purposes.** The reading itself (`route_position`); the use of a reading to place a
customer whose record has no coordinates (`route_place_from_position`); and the kilometres
of the day measured from one reading to the next (`route_road_measured`). The last two are
secondary uses under s. 12 para. 2 (1) of Law 25, each with a direct and relevant connection
to the first: proving one went to a customer and knowing where that customer is; proving the
visits and measuring the road between them.

**ROU-002, six years, for the kilometres.** The figure derived from the readings outlives
them, because a distance locates nobody, and it is printed in the kilometre register. Six
years is what the tax law asks of books and records. Nothing applies it yet, and the rule
says so.

**ROU-001, at most 90 days, for the reading.** Not one day, a setting seen in the field:
the notice the person reads says the position serves to settle a disagreement about
a delivery, and a disagreement is raised days later, so a position erased the next morning
does not serve the purpose it was collected for. Not three years either, the term of
correspondence: a person's position is not correspondence, and three years of readings end
up drawing their days.

**No consent, and that is not an oversight.** Section 8.1 asks that people be informed
before a locating technology is used, and told what means are offered to activate it. It
does not ask for consent, and an employee's consent to being located by their employer would
not be free. What is really offered is simpler: the browser's permission stays theirs, and a
refusal lets the mark go through without a position.

## What it guarantees

**The applied term never exceeds the declared one, and nothing is read before something is
declared.** What erases is `bf_route`'s watcher, and the term it applies is each company's own
setting. With this bridge installed, a company that reads positions cannot be set to keep
them LONGER than the register declares, and cannot begin reading at all while the retention
policy is missing, archived or set to zero days. Both writes are refused, and the message
names the numbers.

A shorter setting is accepted exactly as it stands, and needs nothing declared. Section 23 of
Law 25 asks that information be destroyed once the purpose it was collected for is
accomplished: that is a ceiling, not a floor, destroying sooner is minimisation, and each
entry names the term it actually applied. An earlier form of this guard demanded equality,
which refused a company doing MORE than it had promised and pushed an administrator towards
editing a rule shared by five companies. The cheapest way out of a refusal nobody can act on
is to leave the feature off, which is the opposite of the point.

What this does NOT guard is the register side: raising the declared term later, or archiving
the policy, is a write on the register and not on a company, so no company-field constraint
sees it. A company already out of step is likewise not refused until someone touches one of
its two fields.

**Every erasure is recorded, in partial scope.** The entry names the three fields that left
(`latitude, longitude, accuracy`), the term applied, the rule it falls under, and the stop it
belonged to.

**It says what SURVIVES, too.** An entry that lists only what was destroyed reads as though
nothing was left of the person. Each entry names the stop itself and its times, the person
who marked it, their note, the time on site, the notice as it was read, the kilometres
measured for the day, and a customer's place accepted from a reading. The last two are
derived from the readings and outlive them: a distance is not a place, and a place accepted
by an office person has become the customer's own address.

**It refuses to attest to an emptiness it cannot see.** Before writing, it reads the row back
from the table and raises if the position is still there. Lesson of an earlier bridge, where the
register certified a "CERTIFICATE OF DESTRUCTION" over anonymisations that had rewritten no
field at all. A register that can lie is worth nothing.

That refusal has a cost, and `bf_route` pays it per company: since 18.0.1.4.0 the purge runs
each company inside its own savepoint, so one unverifiable stop keeps one company's
positions for another pass instead of keeping everyone's.

## 🔴 What it does not cover

- **The phone.** A mark waiting for the network keeps its position in the device's browser
  until it is sent. The server's purge does not reach it.
- **The map tiles.** Without `bf_route.tile_url`, the person's browser fetches the map
  background from the OpenStreetMap Foundation, in the United Kingdom, which learns the area
  being looked at. Since `bf_route` 18.0.1.4.0 the notice names that service and says it is
  outside Canada, in two variants; assessing the transfer itself belongs in the privacy
  impact assessment.
- **Backups.** They keep the earlier state for as long as their own rotation does. The
  declared method is therefore `delete`, never `secure_wipe`.
- **The destruction of a route day.** ROU-002 declares six years for the kilometres
  measured, and nothing applies it: no cron destroys a route day, here or in `bf_route`.
  Automatically destroying a tax record deserves its own decision and its own release. The
  rule makes the gap visible; it does not close it.

## Checking it

- *Privacy › Processing register*: the three purposes, and `route_position` with its
  retention policy.
- *Privacy › Retention calendar*: ROU-001, with `bf.route.day.stop` as the model it applies
  to.
- *Privacy › Destruction register*: one entry per position erased, sealed and chained.
- *Settings › Work routes*: changing "Keep positions" to a term the register does not declare
  is refused, with both numbers named.
