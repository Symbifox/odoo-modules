# NFC tags: equipment loan (`bf_nfc_loan`)

A tag on a laptop, a projector, a camera, a key. Bring the phone close:

- the equipment is free: **you take it**;
- you have it: **you return it**;
- someone else has it: the screen names them and offers to take it over.

One tap and no button in the two usual cases. Each piece of equipment keeps the
history of who had it, when and for how long, and a reminder activity goes to
the person keeping it beyond the delay you set.

## Design rules

- **The holder is read from the open loan**, never stored separately: two
  sources of truth end up contradicting each other, and the loan list is the one
  you trust in a dispute.
- **Taking over from someone else is a question**, the only one this gesture
  asks. The previous loan is closed with the name of the person who took over,
  and a note is left on the equipment.
- **Not on behalf of a generic account.** A signed tag acts for a designated
  account, not for the person holding the phone: loans go through the app or a
  session.
- **Offline taps keep their time**, bounded by the core.
- Writing happens with elevated rights **after** the equipment was read with the
  tapper's rights: borrowing a laptop is not a management privilege, and the tag
  is the permission.

In a menu, the parameter `{"sens": "prendre"}` or `{"sens": "rendre"}` fixes the
direction ("I'm taking it" and "I'm bringing it back" buttons).

## Setup

*Tags → Equipment*: create the equipment, then a "Take or return equipment" tag
pointing to it, and engrave it from the app.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
