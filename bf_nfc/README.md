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
session and meeting attendance, meeting room, rounds.

## Design rules

- **Nothing acts on a GET.** Link previews, spam filters and security scanners
  open URLs nobody touched. Through the browser, a gesture that writes goes
  through a one-button confirmation page, and the POST acts.
- **One entry point, `bf.nfc.tag.taper()`.** Every door establishes identity,
  then calls it. It runs the gesture in a savepoint, refuses a second identical
  tap within 20 seconds, and logs every tap, including refused ones.
- **A gesture may ask a question instead of acting.** It returns choices; the
  core rolls back anything the gesture touched and logs nothing. The choice
  comes back in a second call, which is the one that acts.
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

## Label with QR code

Every tag prints as a label with its QR code twin, for phones without NFC. The
QR carries the same address as the chip, so it goes through the same door and
the same confirmation. Signed tags get no QR: their address changes at every
read.

## Mobile API

`/bf_nfc/mobile/v1`: `ping` (public, returns the company's branding), pairing
(`auth/start`, `auth/exchange`, `logout`), `tap`, `pastille/infos`, `pastilles`,
`journal`, `cibles` (target search on a whitelist of models), `catalogue`,
`pastille` (create), `liens` (link pages, when `bf_linkpage` is installed).
Every route runs as the device's person, in their language.

## Configuration

- `bf_nfc.fenetre_doublon_secondes`: duplicate window, default 20.
- `bf_nfc.differe_max_heures`: oldest offline tap accepted, default 72.
- `bf_nfc.modeles_cibles`: models the app may search as targets.
- Signed tags: `bf_nfc.sdm_meta_key` and `bf_nfc.sdm_file_key` (optionally
  suffixed with a company id), system parameters readable by administrators only.

## Tests

HTTP tests play the three doors as an ordinary internal user, the refusals
included: the confirmation barrier, signature and counter checks, pairing and
PKCE, questions that write nothing, menus, offline replay, labels, and the
device safeguards.

## License

Business Source License 1.1, see [LICENSE](LICENSE). Each version converts to
LGPL-3.0-or-later four years after its publication.
