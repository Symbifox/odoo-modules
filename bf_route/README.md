# Symbifox Work Routes (`bf_route`)

Recurring routes for the people who spend their day on the road (delivery
drivers, technicians, sales representatives) on Odoo 18 Community: the stops
in order, a phone screen for the worker, proof at each stop, mileage, alerts,
and the order of the stops computed by a self-hosted engine.

> **Not legal advice.** The position notice and the retention period below are
> a starting point for Québec's *Act respecting the protection of personal
> information in the private sector* (Law 25). Adapt them with your privacy
> officer.

## Why

Odoo Community has `fleet` (vehicles, odometer) and `stock_fleet` (a batch of
deliveries with a vehicle and a driver), but nothing that says in which order
to visit customers, nothing the worker follows on a phone, and no proof that a
stop was made. The route tools on the market send every address and every
position to a third party, usually outside Canada. This module keeps both on
your server (the map's background tiles excepted, see *Known limits*).

## Features

- **Route** (`bf.route`): a template with its stops in order (customer, time
  window, time on site, access instructions), the default worker and vehicle,
  the depot it leaves from and returns to, and the days it runs (weekdays,
  every N weeks, from and until a date), or "on demand".
- **Route days** (`bf.route.day`) are created a few days ahead by a scheduled
  action, in the time zone of the person responsible. A day is never created
  twice: a cancelled day stays cancelled. Changing the template refreshes the
  days that have not started; stops added by hand to one day are kept.
- **"My route"** (`/odoo/my-route`, installable on the phone's home screen as
  an app through `/scoped_app?app_id=bf_route&path=odoo/my-route`): the day's
  stops in order, directions in the phone's own maps app, a call button, a
  note, **"I'm here"** on arrival, and one tap to mark a stop **done**,
  **customer absent** or **postponed**. A postponed stop moves to the next
  planned day of the route.
- **Directions**: on Android, one `geo:` link and the phone offers its maps
  apps (Google Maps, Waze...). An iPhone does not open `geo:` links: it gets
  Apple Maps, Google Maps and Waze, each by its https link (the app opens when
  it is installed, CarPlay follows). On a computer, Google Maps on the web.
  The stop's coordinates (or, when it has none, its address with the postal
  code and the province) go to that app, and only when the worker taps. Never
  the customer's name: a stop with neither coordinates nor address gets no
  directions link.
- **Arrival and departure**: "I'm here" stamps the arrival, once (sent later
  without network, like a mark); the mark that follows is the departure. The
  time on site shows on the day's stops and in *Visits*. Marking without the
  arrival still works. No position is read at the arrival.
- **Map of the day**, on the phone (*Map*) and on the day's form (*Map* tab):
  the depot, the stops numbered in order and coloured by their state, the
  return point, and the road between them from OSRM (straight lines without
  it). A place without coordinates is named under the map. The positions read
  at the stops are drawn beside them, smaller and in another colour, each with
  the accuracy the phone reported as a disc around it: the day as it happened,
  next to the day as it was planned. A route manager sees every reading of the
  day; anyone else sees only the readings they made themselves, which is what
  the mark records, not who holds the day today. Only while the retention
  period keeps them.
- **Without network**: a mark is shown at once and queued on the phone; the
  queue is sent when the network comes back, with the phone's time of the
  mark. Each mark carries a key, so a mark sent twice counts once. A phone
  clock ahead of the server is not trusted.
- **Proof at the stop**: who, when, the note and, only if the company turned it
  on, the position read **once** when the stop is marked. There is no
  continuous tracking.
- **The gap, not the coordinates**: the distance in metres between the
  position read and the customer's place, and what it means. A reading is a
  disc, not a point: its centre is the gap away and its radius is the margin
  the phone reported, so the three answers are about that disc against a
  150 m block. Whole disc inside, "at the place". Whole disc outside, "away
  from the place". Straddling the edge, "within the phone's margin", which
  proves nothing either way. It is symmetric on purpose: a green badge is a
  claim in the person's favour and an orange one a claim against them, and
  both have to be earned. "Away from the place" stays a reading and not a
  verdict, since a large site or a service door at the back land there too.
  Two more states: "customer without a place", and "place itself from a
  reading" when the place being compared with was taken from a position read
  on a route, so the gap compares a reading with a reading.
- **Who reads the gap**: the coordinates, the gap, its state and the margin
  are all the route manager's on the desk screens. The person who MARKED the
  stop reads their own, in words, in the application on their phone: that is
  the right of access of s. 27 of Law 25, served where the person actually is.
  Field-level rights cannot express "mine only" on a stored column, and the
  record rules key on who holds the day, not on who made the mark, so a desk
  column would have handed a reassigned day's new holder someone else's
  reading.
- **A place from a position**: at a customer with no place on the map, the
  position read can be written as the point of their street, by a person
  pressing a button, never by a cron. Refused when the customer already has a
  place of any kind, their own coordinates or a street point the geocoder
  found; when the position has been erased; when the phone did not report its
  accuracy; and beyond 50 m. The contact's chatter says where its coordinates
  came from, and the contact itself carries the date, under *Place on the map
  (routes)*: a gap measured against a place that came from a reading is not an
  independent check of that reading.
- **The road actually driven**: OSRM measures the road from the route's start
  point, through the positions read in the order the stops were marked, back to
  its end point, at the end of the day and on demand (*Measure the road from the
  positions*). The two depot legs come from the route's own points, since no
  position is read at the depot; without them the figure fell short of the
  odometer by the whole way out and back, which is most of a day. Printed beside
  the odometer, never summed into the mileage log, and it stays once measured,
  including after the positions themselves are erased: it is a distance, not a
  place.
- **Position notice** (Law 25, s. 8.1): before any position is read, the
  worker reads the notice and acknowledges it. The acknowledgement keeps the
  text as read. A new text, or turning positions on, asks everyone to read it
  again. Positions are erased after the retention period (90 days by default);
  the stop and its time stay. Two things outlive the position and the notice
  says so: the kilometres measured for the day, which are a distance and not a
  place, and a customer's place accepted from a reading, which has become that
  customer's address. The standard notice changed in 18.0.1.4.0 to say both, so
  that version asks every worker whose company reads positions to read it
  again. A company that replaced the standard text keeps its own: review it
  against those two points.
- **Mileage**: odometer at the start and end of the day, logged to the
  vehicle (`fleet.vehicle.odometer`); planned distance and driving time from
  OSRM.
- **Mileage log** (*Routes › Mileage log*): the business trips of the finished
  days of the year, one line each, with the worker, the vehicle, the odometer,
  the kilometres and the customers driven to (stops done, or customer absent) as
  the purpose of the trip. Grouped by worker, by vehicle or by month; printable
  (*Print › Mileage log*): date, depot, purpose, kilometres, odometer, as
  Revenu Québec and the CRA ask for each business trip. Workers see and print
  their own days.
- **Locate the contacts**: a customer without coordinates is located by a
  self-hosted Nominatim when a route needs it (planned distance, optimization),
  or from the contacts' *Action › Locate on the map (routes)* (200 at a time).
  A result coarser than a street (a town, a region) is refused. A house number
  is written as the contact's coordinates; a street without its number is kept
  apart, as the street point, which plans the kilometres but never guides the
  phone (a rural road can run for kilometres). A contact whose address changes
  (street, city, postal code, province, country: spaces and case aside) loses
  both. This holds for every contact of the database from the install on,
  coordinates typed by hand or set by another module included. A plan stops
  when the geocoder runs out of time (20 seconds per click) and says who is
  not located yet: locate them from the contacts, which keeps what it finds.
- **Optimize the order** with VROOM: time windows and time on site are
  respected; stops that cannot fit are kept at the end and named, never
  dropped. Planned arrival times per stop.
- **Heavy vehicles** (gross weight of 4,500 kg or more): the day cannot start
  until the worker confirms the pre-trip inspection; who and when are kept.
- **Alerts** to the person responsible, one activity per problem, in their
  language: a day not started on time (30 minutes by default), a day left
  open after its date, stops missed at the end of the day.

## Routing engine

OSRM, VROOM and Nominatim are open-source and run on your own server. OSRM and
VROOM get coordinates only, never names or addresses. Nominatim gets the
street, city, postal code, province and country of a contact without
coordinates, never its name, phone or email. Set their addresses in
*Settings › Work routes*. Without them, the order stays manual, the planned
distance is empty and coordinates are entered by hand; everything else works.

Customer coordinates are the contact's latitude and longitude. Nominatim tries
the address with its postal code, then without (a postal code OpenStreetMap
does not know would hide a good street), and keeps a result only when it is a
street or a building.

## Access

| Group | Can |
|---|---|
| Worker on the road | See their own route days and the routes they drive; start, mark and finish their days. No direct write: every change goes through checked methods. |
| Route manager | Build routes, plan, optimize, cancel days, see every day and the positions. Reads every vehicle. |

Coordinates, the gap, its state and the accuracy the phone reported are visible
to route managers only on the desk screens. The person who marked the stop reads
their own gap, its state and its margin as a sentence in the application on their
phone (s. 27 of Law 25: the office cannot be the only one to see what the person's
own phone produced). On the map and on the phone, a reading is shown to the person
who made the mark and to a route manager, never to whoever holds the day
afterwards.

⚠️ Known gap: once the office reassigns a finished day, the person who made the
marks can no longer reach that day at all, so an access request about it goes
through the office. The record rules follow the day's holder; the reading follows
who marked it.

Install `bf_route_privacy` beside this module to declare the three uses of a
reading in the privacy register, to record every erasure, and to keep the
retention setting and the declared term equal: with it installed, a company
that reads positions cannot be set to a term the register does not declare.

## Configuration

1. *Settings › Work routes*: position at the stops (off by default), notice,
   retention, alert delay, OSRM, VROOM and Nominatim addresses, map tiles.
2. Give the contacts their coordinates (Nominatim does it from the address),
   and the depot (the company by default).
3. *Routes › Routes*: create a route, its stops and its days.
4. Workers open *Routes › My route* on their phone.

## Days, the template and the office

- Each stop of a day knows where it comes from: the route, the office, or a
  stop postponed from another day. The template only ever replaces its own
  copies on the planned days nobody touched.
- A day the office edited (another worker, another truck, a stop added or
  changed) is never rewritten by the template: it gets a note instead.
- A postponed stop goes to the next planned day of the route; when the
  customer is already there, its quantities are added to that stop.

## Marks the server refuses

A mark is never thrown away on the phone. A mark refused by the server (the
office deleted the stop, or marked it first) stays on the phone with what it
carried and the reason, at most 7 days and without the position, and is
reported to the office on the day's chatter with one activity per day.

## Companion modules

- `bf_route_sale`: sell from the truck at each stop (truck stock, invoice,
  container deposits, payment recorded on the phone).
- `bf_route_expense` (installs itself with Expenses): the kilometres of a day
  driven with the worker's own car become a draft expense at the per-km rate.
- `bf_route_timesheet` (installs itself with Timesheets): a finished day logs
  the worker's time on the route's project.

## Known limits

- The phone's position works on HTTPS only (browser rule).
- The app needs the network to open: marks wait on the phone, but a phone
  restarted without network cannot open "My route" until it is back.
- Marks waiting for the network keep their position on the phone until sent.
- No signature or photo at the stop.
- The directions link at a stop (Google Maps, Apple Maps, Waze) sends that
  customer's address, or their coordinates, to whichever of the three the person
  presses, outside Canada. It is the one deliberate exception besides the map
  tiles to everything else staying on your server.
- The map's background tiles are OpenStreetMap's by default: the viewer's
  browser fetches them from the OpenStreetMap Foundation (United Kingdom),
  which learns the area looked at (no name, no address). Their usage policy
  allows light use with the credit shown. Point *Settings › Work routes › Map
  tiles* to a tile server of your own to keep that on your side too.
- Accepting a reading as a customer's place changes what every stop of that
  customer is compared with, past and future, including stops someone else
  marked. Those stops read "place itself from a reading", which is the honest
  answer: both points came from the same kind of phone at the same door, so the
  gap confirms nothing. It stays that way after the reading it came from has
  been erased. The contact also carries the date, under *Place on the map
  (routes)*.
- The map's tiles are fetched by the viewer's browser, so opening the map of a
  day shows the tile server the area the readings are in. Since 18.0.1.4.0 the
  standard notice names that service and says it is outside Canada, in two
  variants depending on whether the company set a tile server of its own;
  assessing the transfer itself belongs in the privacy impact assessment.
- The mileage log counts the odometer's kilometres: a day without both
  readings counts 0 km, even when OSRM planned its distance or measured the
  road through the positions. Both are printed beside it, so such a day is not
  blank, but neither is summed into the total: the log answers to Revenu
  Québec, which asks for the odometer. Its "Leaving
  from" is the route's depot, not where the worker actually left from.
- The log holds the business trips of the route days only. For an automobile
  the employer provides, the register Revenu Québec asks the employee to hand
  in by January 10 also needs the days the car was available, the personal
  kilometres and the odometer at the start and the end of the year: keep them
  beside it (the vehicle's odometer readings are in Fleet).
- A pickup at the customer's (as opposed to a delivery) is a stop like any
  other.
- A responsible person without a time zone falls back on the company's.

## License

Business Source License 1.1, see `LICENSE`. Production use for your own
internal operations is allowed; providing it as a service to third parties
requires an agreement with Les services de consultation Blue Fox, Inc.

Bundled: [Leaflet](https://leafletjs.com) 1.9.4 (`static/lib/leaflet`), BSD 2-Clause
License, see `static/lib/leaflet/LICENSE`.
