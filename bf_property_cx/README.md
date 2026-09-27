# Co-ownership: Occupant satisfaction (`bf_property_cx`)

The bridge between the occupant portal's maintenance requests and the `bf_cx`
listening programme. It does one thing: when a request is **settled**, it asks
the person who filed it how it went, in three emoji.

An auto-installing bridge, on the `bf_cx` pattern: it installs itself when both
sides are present, and the co-ownership suite works perfectly without it.

## What it does

| Where | What |
|---|---|
| `bf.property.organisation` | A switch, `cx_feedback_enabled` ("Measure occupant satisfaction"), off by default, on the organisation form with a note saying what it does in each position. |
| `bf.property.request` | Gains `rating.mixin`. The rated person is the requester (`requester_partner_id`), not a `partner_id` the model does not have. A read-only flag, `cx_feedback_sent`, records that the request has already asked once. |
| `action_done` | After the request is settled, sends the feedback request to the requester, in the requester's language, and marks the person as solicited in `bf_cx`. |
| Mail template | "Your request has been settled: how did it go?", with the request name, the organisation, the written resolution, three rating links (satisfied, fair, dissatisfied) that accept a free comment after the click, and the `bf_cx` unsubscribe link. |

The answer lands in the `bf_cx` experience register like any other rating.

## A settled request, never a refused one

The hook is `action_done`, and only it. Refusing, reopening or archiving a
request says nothing about a service rendered.

`action_refuse` requires the syndicate to say why the request falls outside its
object under art. 1039 CCQ: the conservation of the immovable and the
administration of the common portions. Asking "how did it go?" straight after a
refusal measures the refusal, not the service, and nobody needs software to
guess the answer. The module stays silent, and a test enforces it: this is
exactly the behaviour a later shortcut would add "to cover every case".

## One request per request

A settled request sends one feedback request. Reopening and closing it again
does not send a second one: the person has already been asked.

## One switch here, not two

The text-message bridge carries two switches, because a phone number is not in
the register by right: art. 1070 para. 1 CCQ puts the name and postal address
there, and other personal information only with the person's express consent.
Nothing of the sort applies here. The module puts no email address in the
register. It writes to the person who has just written to the syndicate, at the
address they used, about their own request.

What remains true is that a request for feedback is a solicitation. It has two
brakes that do not come from this module: the `bf_cx` anti-oversolicitation
guard, which counts per person rather than per syndicate, and the unsubscribe
link every email carries. The syndicate's switch is the third, and it comes
first. It is off by default.

When the `bf_cx` register is set not to ingest ratings (`bf_cx.ingest_ratings`),
nothing is asked either: a question whose answer would go nowhere would solicit
someone for nothing.

## The email comes from the syndicate

The template body names the organisation and carries no software name. The
frame around it is the company's: the request goes out through an override of
`rating_send_request` that uses the suite's mail layout (`bf_mail_layout` from
`bf_property_core`), which is the Symbifox branding module's layout when that
module is installed and Odoo's light layout otherwise. The branding module is
not a dependency.

The button colour is the company's report brand colour when that field exists,
and Odoo's neutral colour otherwise. The template calls no field of a module
that might be absent, so it never breaks and never prints one company's colour
on another's mail.

## A failed request never reopens a settled one

The janitor has just said what was done. That statement does not get lost
because an email failed to leave. The feedback request runs inside a savepoint;
a failure goes to the server log and to a line in the request's thread, the
request stays settled, and the resolution stays written.

## What the syndicate sees when nothing arrives

Every refusal to send leaves a readable line in the thread: no email address in
the register, the person was solicited recently, or the sending failed. A
syndicate that has opened the measurement and receives nothing can find out why
without opening a server log.

## Security

No group or rule of its own. The switch is edited on the organisation form by
whoever may edit the organisation. Whether a person may be solicited is read
with elevated rights, and only that read: it is a rule of the system, not a
right of whoever closes the request. A co-ownership manager closing a request
therefore gets the feedback asked without holding the privacy group.

## Dependencies

- `bf_property_portal`
- `bf_cx`

## Tests

The tests cover the switch being off at creation, silence while it is off, the
settled request asking its requester, the refused request never being
measured, the missing email written to the thread, no second request, the
anti-oversolicitation guard, a failed send that still closes the request, the
absence of any software name in the template, a real manager account closing a
request, the failure line in the thread, and the branded layout.

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
- **Change Date**: on 2030-08-24, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
