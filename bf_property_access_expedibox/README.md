# Property: Expedibox Parcel Locker (`bf_property_access_expedibox`)

Connector between resident access (`bf_property_access`) and an
[Expedibox](https://www.expedibox.com) parcel locker. A parcel point wired to
an Expedibox connector no longer sends its orders to the caretaker: Symbifox
registers the resident with the locker when they move in, and removes them
once their departure is confirmed.

## What it adds

- **A connector kind, `Expedibox: parcel locker`.** It carries the application
  token of the *client organisation* (each organisation that installs the
  Symbifox application on its locker gets its own, from Expedibox's developer
  portal), the Expedibox organisation id (noted for parcel notices, not used
  by this connector) and the choice of what the locker is told in order to
  reach the resident. The token is readable by property managers only, not by
  Consultation users.
- **A locker id on the access point.** One parcel point, one locker. A
  connector of this kind refuses any point that is not of type *Parcel locker*,
  and refuses a parcel point without a locker id.
- **Registration and removal.** An *add* order creates the recipient
  (`POST /recipients/`), a *remove* order deletes it
  (`DELETE /recipients/{id}/`). What is sent: first name, last name, unit, one
  way of reaching the resident, and a reconciliation identifier. Nothing else
  — no landline (a parcel notice texted to a landline reaches nobody), no
  lease date, no code.
- **Residents only.** A grant carried by a temporary access (visitor,
  contractor, staff) is never sent to the locker: the locker has no validity
  window of its own, and the privacy notice covers residents. That order goes
  to the point's manager, who does it by hand if the organisation wants it.
- **A nightly pass keeps the locker in step.** A name or a unit corrected on
  the file is carried to the locker (art. 28 of the private-sector Act, the
  same article the EFVP template already cites for the right of correction: a
  correction has to follow what was disclosed, and the locker shows the name to
  the courier). A recipient registered by this database that no live grant
  claims any more is reported to the point's manager — never deleted on its
  own.
- **Minimisation by default.** Expedibox requires at least one way to reach the
  resident, since the locker itself tells them a parcel is waiting. By default
  only one is sent: the e-mail, or the mobile number when there is no e-mail.
  The connector can be set to send both.
- **An EFVP (privacy impact assessment) section**, listing what the locker
  receives, why a contact detail is needed, and what has to be settled outside
  the software: the written agreement (art. 18.3), where the supplier hosts
  the data and the prior assessment if that is outside Québec (art. 17).

## Safety

- **The module only ever touches its own registrations.** Every recipient it
  creates carries a `custom_identifier` naming this database, the person and
  the point (`sfx-<database>-p<partner>-pt<point>`). Before removing anyone,
  the module reads the recipient back and checks both that identifier and the
  locker: a reference typed by hand that points at another system's recipient,
  or at another locker, is refused, and the removal goes to the point's manager
  instead. A demo or a bench wired to a client's locker by mistake recognises
  none of its recipients, so it removes none. Without a `database.uuid` the
  module refuses to call at all, rather than share a prefix with another base.
- **The identifier survives a rolled-back transaction.** The base module pushes
  inside the transaction that creates the grant. Were the identifier built on
  the grant's id, a transaction that failed *after* the locker answered would
  leave a recipient that no grant could ever claim or remove again — the grant
  id is burnt, a sequence does not go back. The person and the point already
  existed, so the grant created on the next pass finds its recipient, adopts
  it, and no duplicate is made.
- **Replayable.** An *add* order first looks for its own recipient: a replayed
  order never creates a second one, and a name or unit that drifted meanwhile
  is corrected. A removal whose recipient is already gone (404) closes as done.
- **It never concludes that there is nobody to remove.** A remove order only
  exists for a grant that may have been posted: if no registration of ours can
  be found at the locker, it was made there by hand (or lost), so the order
  goes to the point's manager with that sentence. Closing it as done would
  leave the person at the locker with a journal saying the opposite.
- **Changing a point's locker id removes and re-issues.** For an Expedibox
  point, the locker id *is* the point's scope — it decides which physical door
  opens. Residents are removed from the old locker (while the field still names
  it) and registered with the new one, as the base module already does for its
  own four scope fields.
- **A refusal does not wait for three tries.** No token, no locker id, a
  resident without e-mail or mobile, or data refused by Expedibox (4xx) hands
  the order to the point's manager immediately, with the reason. Only an
  outage, a timeout, a 429 or a 5xx leaves the order to be sent again; the
  nightly run replays it.
- **The locker never waits for Odoo to open.** It validates its own
  recipients; an outage delays a registration or a removal, it never blocks a
  parcel.
- The API host is a constant (`https://api.expedibox.com`): no field and no
  setting can send a request anywhere else, and the session refuses the
  environment (`trust_env = False`), so no proxy, CA bundle or `~/.netrc` can
  divert it. Calls carry an explicit `User-Agent` (Cloudflare answers 1010 to
  the default one), a timeout, and follow no redirect; a redirect is read as
  "this is not the API" and the order goes to the manager. A reference is
  URL-quoted before it reaches a path.
- **No bulk endpoint.** `/recipientsBulk/` is never used: its
  `delete_other_recipients` would wipe the recipients of every other system
  sharing the locker.
- Expedibox answers `202` on a delete only to say "accepted": it is not read as
  "removed", and the order is sent again until an answer concludes.

## Limits

- A rename or a move is carried by the nightly pass, not instantly: the base
  module raises no order for it (it only knows *add* and *remove*).
- A name of a single word, and a legal person, fill both name fields, because
  Expedibox refuses an empty one.
- Two points may share one locker id, legitimately, when each serves its own
  unit (an assigned locker): nothing refuses it, and nothing warns when it is a
  typo either. The connector's "Lockers this token sees" button is how to get
  the right id.
- The notice, the register and the EFVP section that name what the locker
  receives live in `bf_property_access_privacy` **18.0.1.0.3**. On a base
  carrying an older one, the software would disclose a contact detail to a
  third party with a notice that does not name it.
- What the locker *displays* belongs to the locker: its screen is how a courier
  picks a recipient, so the resident's name appears on it. `visibility` is left
  to the locker's own setting; the EFVP section says so and asks for it to be
  checked with the supplier.
- A resident of a room with no unit on file is sent with `-` as the unit, which
  Expedibox requires.
- Parcel notices (deposit and pickup, webhook `reservation_event`) are another
  piece of work, not part of this module.

## Dependencies

`bf_property_access`. Python: `requests`.

## Tests

`odoo -u bf_property_access_expedibox --test-enable --test-tags /bf_property_access_expedibox`

Nothing leaves the bench: `requests.Session.request` is replaced for every
test in the file — creating a point is enough to make the base module push — and
each test states what Expedibox answers; an unexpected call fails the test. The
tests measure what is actually sent — method, path, headers, body — not what
the module believes it sent.

Every guard above was also checked by mutation: breaking it in turn has to
make a test fall.

## Licence

BUSL-1.1, see `LICENSE`.
