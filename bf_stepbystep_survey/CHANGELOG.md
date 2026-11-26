# Changelog

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
