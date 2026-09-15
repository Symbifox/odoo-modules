# NFC tags (`bf_nfc`)

An NFC tag triggers nothing on its own. It carries a few dozen bytes, and in
practice the only format both mobile platforms read without an app is a link.
The phone opens it, and the server acts.

This module is that server: a register of tags, a catalogue of gestures, a log
of every tap, and three doors to come in through. It is the core of the
**Symbifox Pastilles** family; the Android app of the same name is distributed
through the Symbifox F-Droid repository.

## What a tag carries, and what it never carries

A short public code, and nothing else. A tag can be read from four centimetres
without consent or trace, and copied onto a blank chip for less than a dollar:
what you engrave is a poster, not a secret. Identity comes from elsewhere, and
that is what separates the three doors.

| Door | Where identity comes from | For whom |
|---|---|---|
| **App** | the bearer token of a paired phone | staff, on Android |
| **Browser** | the Odoo session already open | anyone with an account, any phone |
| **Signed tag** | an NTAG 424 DNA chip that signs every read (AES-CMAC, monotonic counter) | a tag handed to someone without an account |

## Gestures

| Gesture | What it does | Writes |
|---|---|---|
| Open the record | opens the record the tag points to, with the reader's rights | no |
| Open an address | opens an `https`/`http` address, nothing else | no |
| Log a visit | posts a dated internal note on the record (a note, never an email) | yes |
| Offer a menu | shows a few buttons, each playing its own gesture | yes |
| Run a scheduled action | triggers a cron now (managers only) | yes |
| Run an action | runs a server action with the tapper's rights (managers only) | yes |

Satellite modules add their own: timer, equipment loan, report a problem,
session and meeting attendance, meeting room, rounds, readings with a checklist,
and Gen skills.

## Design rules

- **Nothing acts on a GET.** Link previews, spam filters and security scanners
  open URLs nobody touched. Through the browser, a gesture that writes goes
  through a one-button confirmation page, and the POST acts.
- **One entry point, `bf.nfc.tag.taper()`.** Every door establishes identity,
  then calls it. It runs the gesture in a savepoint, refuses a second identical
  tap within 20 seconds, and logs every tap, including refused ones.
- **What a tag does is fixed when it is created, never when it is tapped.** A
  tag's parameters come from the tag alone: nothing the browser's query string or
  the app's request body carries can replace them. (Up to 18.0.2.2.0, `?url=`
  replaced the engraved address and `equipe` sent a ticket to a team of the
  caller's choosing.)
- **A gesture may ask a question instead of acting.** It returns choices, and
  optionally a `formulaire` (fields typed `conforme`, `nombre`, `choix`, `texte`);
  the core rolls back anything the gesture touched and logs nothing. The choice
  and the answers (`reponses`) come back in a second call, which is the one that
  acts.
- **Offline taps are bounded.** The app queues taps made without a network and
  sends them later with the time they were made and a nonce. The same send
  replayed returns the line already written; a time more than 2 minutes in the
  future or 72 hours in the past is refused; gestures that only make sense on
  site (opening a record, starting a timer, taking a room) refuse offline taps.
- **Menus replay each line's safeguards.** A choice that runs a manager-only
  gesture is refused to others, even if its key is forced.
- **Paired devices:** PKCE S256 mandatory, token stored hashed, at most 10
  devices per person, deactivated after 180 days without a call. A device's
  owner cannot be changed, and deactivating a device erases its token in the
  same write.
- **The log keeps the person and the tag, never the IP address.** Behind a
  proxy, an address says which machine opened the URL, not who tapped.

## Administration

Everything a tag manager needs lives in the Tags app, not in the system settings,
because Odoo's Settings are reserved to system administrators.

- **Target record** is picked as a record type then a record, from a whitelist
  shared by the website and the app (*Configuration → Record types*). Types that
  trigger processing (scheduled actions, server actions) are offered to managers
  only. Models a gesture requires are added automatically.
- **Settings** (*Configuration → Settings*): duplicate window, offline delay, and
  where the encryption key of signed-tag keys lives. Pairing schemes stay with
  system administrators.
- **Signed-tag keys** (*Configuration → Signed tag keys*): one AES pair per
  company, entered through a wizard that encrypts them (Fernet) and forgets the
  plain text. A key is never displayed again. The encryption key is read from
  `BF_NFC_FERNET_KEY`, then `bf_nfc_fernet_key` in `odoo.conf`, then generated in
  the database; the settings page says which, and what it protects: a key kept in
  the database protects against reading on screen or through the API, not against
  a copy of the database.
- **Batch creation**: *Create tags* on contacts, equipment, rooms and rounds (one
  tag per record, or per checkpoint), skipping records that already have one for
  that gesture. Labels print from the list of created tags.
- **Back-links**: a *Tags* smart button on the records tags point to.
- **History**: tags and gestures track what changes what a tag does (gesture,
  target, parameters, signed-tag account), and carry activities.

## Templates

A template is a recipe: one line per tag (`name ; place`), one click. The core
recipe creates one tag per line with the template's gesture, parameters and menu;
satellites add theirs (a round and its checkpoints, equipment and their loan tag,
rooms). Sixteen templates ship with the family, for fire safety, occupational
health and safety, offices, buildings and childcare. Shipped templates and
checklists are `noupdate`: a customer adapts them, and an upgrade does not
overwrite that.

## Label with QR code

Every tag prints as a label with its QR code twin, for phones without NFC. The
QR carries the same address as the chip, so it goes through the same door and
the same confirmation. Signed tags get no QR: their address changes at every
read.

## Mobile API

`/bf_nfc/mobile/v1` (api 2): `ping` (public, returns the company's branding), pairing
(`auth/start`, `auth/exchange`, `logout`), `tap`, `pastille/infos`, `pastilles`,
`journal`, `cibles` (target search on a whitelist of models), `catalogue`,
`pastille` (create), `liens` (link pages, when `bf_linkpage` is installed).
Every route runs as the device's person, in their language.

## Configuration

All of it from *Tags → Configuration*. Underneath:

- `bf_nfc.fenetre_doublon_secondes`: duplicate window, default 20.
- `bf_nfc.differe_max_heures`: oldest offline tap accepted, default 72.
- `bf.nfc.target.type`: record types a tag may point to (migrated in 18.0.2.3.0
  from the former `bf_nfc.modeles_cibles` parameter).
- `bf.nfc.sdm.key`: encrypted signed-tag keys per company (migrated in 18.0.2.3.0
  from the former plain-text `bf_nfc.sdm_meta_key` / `bf_nfc.sdm_file_key`
  parameters, which are erased).

## Tests

HTTP tests play the three doors as an ordinary internal user, the refusals
included: the confirmation barrier, signature and counter checks, pairing and
PKCE, questions that write nothing, menus, offline replay, labels, and the
device safeguards. Administration tests run as a tag manager who is NOT a system
administrator (no rights on `ir.model`), and every shipped template is applied
with its own example lines.

## License

Business Source License 1.1, see [LICENSE](LICENSE). Each version converts to
LGPL-3.0-or-later four years after its publication.
