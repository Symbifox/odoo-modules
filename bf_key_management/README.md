# Symbifox Keys (`bf_key_management`)

Physical keys, access cards, fobs and remote controls of the premises: who holds
what, since when, which doors each one opens, and what must come back when
someone leaves.

## What it does

- **Three separate records**, as key control policies require: the *key type*
  (its symbol in the master key system, e.g. AA1), each *copy* (its stamped
  number, its holder, its state) and the *door* (its cylinder).
- **Master key systems** of two to four levels (change key, master, grand
  master, great grand master). The doors a key opens are computed from the
  hierarchy: a master opens every door of the keys below it. A cylinder marked
  *not master keyed* opens with its own key only. Cross keying is flagged, and
  symbols are checked against the Standard Key Coding System (A, AA, AA1).
- **Hand-out from the screen**: copy, holder (an employee, or a contractor or
  volunteer without an employee record), expected return date (the end of the
  need), deposit, signature on the screen, printable acknowledgement.
- **No direct transfer**: a copy held by someone must come back before it is
  handed out again. The database enforces it too.
- **Requests**: an employee asks for doors or a key, with a reason and an end
  date. The person responsible for the area approves (on by default for master keys). The
  request warns when the person already holds something that opens those doors.
- **Lost or stolen**: the hand-out closes, an incident freezes the list of
  compromised doors, and the key manager gets the decision to make: rekey those
  doors, or deactivate the card in the access system.
- **Departures**: when an employee is archived or gets a departure date, a
  *Recover* activity is created for each item still held. It closes itself when
  the item comes back. Archiving is not blocked.
- **Reminders**: past the expected return date, the holder (or the key manager,
  for someone without an account) gets one activity.
- **Audits**: every hand-out or a sample of holders (a quarter by default). Holders
  confirm from *Mine → Keys to confirm*; the key manager records what they saw;
  what is left is a list of discrepancies.
- **Overview**: handed out, overdue, to recover from people leaving, cards to
  deactivate, master keys out, open losses, requests to approve, systems due for
  review (every five years by default).
- **Reports**: acknowledgement, keys held by a person, who can open a door,
  hand-out register.

## Design rules

- **The holder is read from the open hand-out**, never stored beside it.
- **Labels carry a blind code and the copy number**, never the door, the level
  or the address (insurers exclude keys that carry the address).
- **Symbols and master key systems are read by area responsibles and key
  managers only**: they describe the security of the premises.
- **Quebec Law 25**: the name, phone and employer of a person without an
  account, and the signature, are erased after a retention period you set
  (three years by default, *Keys → Configuration → Settings*).

## Roles

| Group | Can |
|---|---|
| Key holder (every internal user) | see their own keys, confirm them during an audit, ask for a key |
| Area manager | approve the requests of their areas, hand out and take back their keys, declare losses |
| Key manager | everything: master key systems, doors, audits, settings |

## Setup

1. *Keys → Configuration → Settings*: choose the key manager.
2. *Premises → Areas*: buildings and floors, with their responsible people.
3. *Premises → Master key systems*, then *Key types* (with their blind code) and
   *Doors* (the key each cylinder is keyed to).
4. *Keys → Copies*: one line per physical copy, with its stamped number.

## Not in this version

Signature through `bf_sign`, NFC tags on keys, holder confirmation by portal or
email, a direct link to an access control system, fees posted to accounting.
Loaned equipment and the lockout padlock register stay in `bf_nfc_loan`.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
