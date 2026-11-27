# Changelog

## 18.0.1.2.0 (2026-11-27)

The button picks up the answer the company **already has**. When the client
received the questionnaire through a personal link in an e-mail, the first click
on the page now leads to that answer instead of creating a second one.

Taken over: an answer to the same questionnaire, in the name of a contact of the
company the page names, **created within the 90 days before the click** (the
invitation often leaves before the mandate exists), not a test entry, whose
deadline has not passed, that has not used up its time limit and that no mandate
holds (archived ones included). The most advanced wins (finished, then started,
then new), then the most recent. The invitation's deadline is dropped once taken
over: the page becomes the mandate's channel.

Nothing is taken over when the page names the mandate's **payer** and that payer
looks like an ordering customer: another of its mandates names a different
beneficiary or carries the same questionnaire (without a bridge
that names the beneficiary, any other mandate of the payer counts). A page
that names a beneficiary is not concerned: the beneficiary is who reads it.

Each takeover leaves an internal note on the mandate. A project administrator
can **Detach the answer** from the *Client portal* tab: the answer is kept, the
client's next click starts a new one, and from then on this mandate takes over
nothing by itself.

An answer already finished is attached and the click leads back to the page,
which says *Questionnaire received*. The same goes for a button left open in
another tab after the answer was finished: the route used to answer 404, it now
returns to the page. The answer's token still never travels in an address.

## 18.0.1.1.1 (2026-11-25)

The stage that asks the questionnaire and the mandate's answer are reserved to
**Project: Administrator**, like the questionnaire itself. Being read-only in the
form protected nothing from a direct write. With Odoo's standard rights only a
project administrator can write a project, so this closes a gap for databases
that grant project write access more widely, and it stops project users from
reading the mandate's answer id.

## 18.0.1.1.0 (2026-11-25)

The questionnaire completed from the progression page notifies the mandate
manager, through the channels set in the core's settings (activity, Odoo
notification, e-mail; `bf_stepbystep_clients` 18.0.2.8.0 or later). Only the
answer the mandate serves counts, and only once: the same questionnaire answered
elsewhere, or an answer detached when the mandate's questionnaire changed,
notifies nobody. An answer closed by the time limit says so instead of
"completed". On an older core, nothing is notified and the client's submission
still goes through.

## 18.0.1.0.2 (2026-11-17)

One more test, no behaviour change: the route closes when the mandate asks for
an access code. The core's promise that every public path inherits the code
guard is now measured on a bridge, not only on the core's own deposit route.

## 18.0.1.0.1 (2026-11-17)

A mandate without a questionnaire no longer leaves an empty block on the client's
page: the template checked the stage, not whether there was anything to put in
it.

## 18.0.1.0.0 (2026-11-17)

First version. The client opens the mandate's questionnaire from its progression
page: a button in the stage that asks for it, or once under the page heading. The
answer is tied to the **mandate**, not to its payer, so two mandates of the same
ordering customer never read or overwrite each other. A finished answer is never
reopened, and the page says "Questionnaire received" instead. The route is a POST
under the same guard as the page, so nothing that merely follows a link can create
an answer. Needs `bf_stepbystep_clients` 18.0.2.6.0 or later, whose page and
form anchors it hangs on.
