# Federation (`bf_federation`)

Two Odoo/Symbifox instances that trust each other exchange tasks, without anyone
needing an account on the other side. Every instance both sends and receives.

## Pairing (administrators only)

1. On instance A: *Federation › Peers*, create the peer, **Generate an invitation**.
   Hand instance B, over a safe channel, A's address and the code (valid 48 hours,
   single use).
2. On instance B: *Federation › Accept an invitation*: A's address, the code, the
   user who receives tasks, and whether B's internal notes should travel to A.
3. B generates a shared secret and presents it to A together with the code; both
   peers become active. **Test the connection** sends a signed empty message and
   waits for the answer.

Every message is signed (HMAC-SHA256 over timestamp, nonce and body), the
timestamp is bounded to five minutes, and a nonce is accepted once. The secret
travels once, at pairing time, over HTTPS.

## Sharing a task

Set **Federated with** on the task, or use the bulk action **Federate with…**
(project managers). Only projects that list a peer under *Federation peers* offer
the field; a constraint refuses the rest. The mirror appears on the peer inside a
closed project "*Peer* (federated)" (followers-only visibility, no partner, three
stages), assigned to the user the peer chose.

| Travels | Never travels |
|---|---|
| name, description reduced to text (bullets and links kept) | hours, timesheets |
| deadline day (in the sender's time zone) | partner, tags |
| priority, state (flipped) | internal notes, unless the peer opted in for its own side |
| "Send message" messages, attachments under the cap | attachments above the cap (named only) |

**The state flips**: "Waiting - Client" on the sender becomes "In progress" on the
receiver; "Done" on the receiver sends the task back "In progress" to the sender.

**A message or note that starts with 🔒 or [private] stays with its author.**

Removing the peer, archiving or deleting the task **archives** the mirror; nothing is
ever deleted on the other side.

## Outbox

Every send is journaled (*Federation › Outbox*), retried with a growing delay when
the peer does not answer, and abandoned after twenty attempts or on a definitive
refusal. Cron every two minutes.

## Requirements

Odoo 18, `project`, `mail`. The optional `bf_task_waiting_states` module adds the
"Waiting - Client" and "Waiting - External" states the flip table uses; without it
the module falls back to the standard "Waiting" state.

## Tests

`--test-tags federation`: pairing, signature and replay, sharing, state, messages,
notes, the 🔒 marker, attachments, deadline, card, removal, retry, access rights,
project scope. The tests pair the instance with itself.

## Licence

LGPL-3. © Les services de consultation Blue Fox, Inc.
