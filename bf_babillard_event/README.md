# Babillard : événements (`bf_babillard_event`)

A bridge between the noticeboard (`bf_babillard`) and Events (`event`). It installs itself as soon as both modules are present.

An upcoming event appears on the noticeboard as soon as it is created, with its date. Its card drops off by itself the day after the event. An event already in the past does not appear.

The card follows the event:

* when the event is renamed or moved, the card is updated;
* when it is cancelled, finished or archived, the card leaves the feed;
* if it becomes upcoming again, the card comes back.

In Odoo 18, events no longer have a `state` field. The bridge reads the stage and its `pipe_end` flag instead.

## Licence

Business Source License 1.1; see `LICENSE`. Each version converts to LGPL-3.0-or-later four years after its release.
