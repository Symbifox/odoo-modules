# Federation (`bf_federation`)

Two Odoo/Symbifox instances that trust each other exchange tasks, without anyone
needing an account on the other side. Every instance both sends and receives.

## Pairing (administrators only)

1. On instance A: *Federation › Peers*, create the peer, **Generate an invitation**.
   Hand instance B, over a safe channel, A's address and the code (valid 48 hours,
   single use).
2. On instance B: *Federation › Accept an invitation*: A's address, the code, the
   user who receives tasks, and whether B's internal notes should travel to A.
3. B presents the code and its half of the secret; A answers with its own half. The
   shared secret is derived from both halves, so neither instance chooses it alone.
   Both peers become active; **Test the connection** sends a signed empty message
   and waits for the answer.

Every message is signed (HMAC-SHA256 over timestamp, nonce and body), the
timestamp is bounded to five minutes, a nonce is accepted once, and the body is
capped at 8 MiB. Only `https://` addresses are accepted (a system parameter,
`bf_federation.allow_http`, tolerates plain HTTP on a test bench). Errors returned
to the peer are generic. A received message is written under a local person's
name **only** when the peer's identity table names them; otherwise under the
peer organisation's name, with the announced name as a prefix.

## Sharing a task

Set **Federated with** on the task, or use the bulk action **Federate with…**.
Federating, withdrawing or changing the peer requires the project manager role,
whichever door is used. Only projects that list a peer under *Settings › Federation
peers* offer the field; a constraint refuses the rest. The mirror appears on the peer inside a
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

Removing the peer, archiving or deleting the original task **archives** the mirror;
nothing is ever deleted on the other side. The other way round, a receiver who
archives, deletes or detaches its mirror never touches the original: it gets a note
and stops being federated. A mirror cannot be federated to a third peer. Dragging the
mirror into "Done" finishes the receiver's part and sends the original back "In
progress"; a column change that recomputes the state travels too.

## Outbox

Every send is journaled (*Federation › Outbox*, administrators), retried with a
growing delay when the peer does not answer, and abandoned after fourteen days or on
a definitive refusal. One queue per task: a message never leaves before the share
that precedes it; a peer that does not answer is skipped for the rest of the pass.
A send replayed after a lost reply is recognised by the receiver, with no duplicate.
Cron every two minutes; successful sends are purged after seven days.

## Requirements

Odoo 18, `project`, `mail`. The optional `bf_task_waiting_states` module adds the
"Waiting - Client" and "Waiting - External" states the flip table uses; without it
a task waiting on the other side simply stays "In progress". Source strings are
French, with a `fr_CA` catalogue; menus read *Fédération › Pairs*, *Accepter une
invitation*, *Tâches fédérées*, *Liens*, *Boîte de sortie*.

## Language

Source strings are written in French, and the module ships **no** `fr_CA`
catalogue: it would have nothing to translate, and an identity catalogue freezes
one release's labels onto the next. The `i18n/bf_federation.pot` template is
provided for translating into another language.

## Tests

`--test-tags federation`: pairing and unexpected types, signature and replay,
sharing, state both ways, column changes, messages, notes, the 🔒 marker,
attachments, deadline, card, removal and re-share, deletion, receiver cleanup, queue
order and lost replies, escaped notes, access rights, project scope. The tests pair
the instance with itself.

## Licence

LGPL-3. © Les services de consultation Blue Fox, Inc.
