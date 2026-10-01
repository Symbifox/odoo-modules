# Symbifox Helpdesk: Duplicates, Merge and Incidents

Companion module for `bf_helpdesk`. It catches duplicate tickets as they arrive,
makes ticket merging complete and safe, and groups tickets that share one cause
under a parent incident that can be answered in one go.

Nothing is merged, linked or sent to a client without an agent's action.
`bf_helpdesk` itself does not depend on this module: install it only if you want
these features.

## Features

### Duplicate suggestions at creation

When a ticket is created, the module looks for open tickets of the same company,
created in the last 7 days, from the same contact, the same email address or the
same organization. Similarity combines the subjects (60 %) and the descriptions
(40 %), compared on their significant words (accents and stop words removed);
from 35 % the other ticket is suggested.

- **Suggestion only**, in a banner on the ticket form.
- Same contact: "Same request? Merge." Same organization, different contact:
  "Same cause, different requester? Link to an incident."
- Buttons: **Merge** (same contact only; the oldest ticket is kept),
  **Incident**, **Not a duplicate**.
- Each suggestion keeps its outcome (suggested, merged, linked, dismissed), so
  you can measure how accurate the suggestions are.

### Complete merge

Builds on the OCA merge wizard (`helpdesk_mgmt_merge`), which moves messages,
activities, tags, attachments and followers to the kept ticket and archives the
others. This module adds:

- **Timesheets** move to the kept ticket, so they stay in its project and hour
  bank instead of sitting on an archived ticket.
- **Internal notes stay internal**: they move with the other messages and keep
  their internal status.
- **Merge notices are internal too**: they are posted as internal notes, never
  as a public message emailed to the clients of both tickets. A notice stays on
  the merged ticket as a trace.
- **Trace on both sides**: a "merged into" banner on the merged ticket, a
  "Fusionnés (n)" button on the kept one.
- A clean HTML description combining both tickets.
- A survey scheduled on the merged ticket is cancelled, and its "waiting for
  client" state is lifted (no more reminders).
- Client replies to earlier emails still land on the kept ticket: moved messages
  keep their Message-ID.
- **Refuses to merge tickets from different clients** (or different companies),
  the destination ticket included: merging would show one client the other's
  messages. Link them to an incident instead.
- **Agents with access to all tickets can merge**, not only administrators (the
  merge wizard of `helpdesk_mgmt_merge` is granted to "User (all tickets)"; the
  Merge button is hidden from other agents). Write access is checked on each
  ticket, then the merge runs with the rights it needs, and the agent stays the
  author.

### Parent incidents

- A ticket can be the parent incident of other tickets, on one level only.
- Link tickets from the form, from the duplicate banner, or in bulk from the
  ticket list (action "Rattacher à un incident"), to an existing or a new
  incident.
- **Grouped reply** from the incident: the message is posted on each open child
  ticket, **in each client's own thread**. No collective email, no client sees
  the others.
- Optionally close the child tickets at the same time: they are closed before
  the message is posted, so the message becomes the resolution, reaches even a
  client who muted the request, and no second closing email follows. A summary
  note is left on the incident.
- **Create a problem from a theme**: on a helpdesk theme (Reports › Thèmes, from
  `bf_helpdesk`'s weekly themes), "Créer un problème" links the theme's recent
  tickets to a parent incident.

### Related tickets

`helpdesk_ticket_related` (OCA) adds symmetric "related tickets" links, with no
hierarchy, for everything that is neither a duplicate nor an incident. Its tab is
labelled "Billets liés", in line with the rest of the French ticket form.

## Dependencies

| Module | Source |
|---|---|
| `bf_helpdesk` | this repository |
| `helpdesk_mgmt_merge` | OCA, [helpdesk](https://github.com/OCA/helpdesk) repository, branch 18.0 |
| `helpdesk_ticket_related` | OCA, [helpdesk](https://github.com/OCA/helpdesk) repository, branch 18.0 |

## Configuration

None. Once installed:

- the duplicate banner appears on new tickets;
- the "Merge" action is available from the ticket list;
- the incident buttons appear on the ticket form, and the bulk "link to an
  incident" action in the list;
- "Créer un problème" appears on helpdesk themes.

Duplicate suggestions follow the company of the ticket (company record rule).

## License

AGPL-3, in line with its OCA dependencies, which are AGPL-3.

It depends on `bf_helpdesk`, so the practical limitation on redistribution
described in `bf_helpdesk`'s README applies here as well. If you need other
terms, [talk to us](https://symbifox.com).

## Changelog

| Version | Change |
|---|---|
| 18.0.1.3.2 | Linking a ticket to a parent incident requires read access on that incident. |
| 18.0.1.3.1 | The client and company checks of a merge include the destination ticket: merging into another client's ticket is refused. |
| 18.0.1.3.0 | Onchange calls on duplicates, incident wizards and the merge wizard refuse tickets the user cannot read. Incident and merge wizard rows are visible to their creator only. The merged-into and parent-incident links are not readable from the portal. The Merge button of the duplicates list is shown only to agents allowed to merge. |
| 18.0.1.2.2 | The candidate fields of a duplicate pair and the child count of an incident reply are read with the user's rights instead of elevated rights, so they never show a ticket the user cannot see. |
| 18.0.1.2.1 | Duplicate detection, which runs with elevated rights, now requires write access on the tickets it is called on. A duplicate pair passed by the caller is closed only when it concerns the tickets being merged or linked. Duplicate pairs are visible only when both tickets are visible to the user, and the duplicate warning on the ticket form is shown from the "Team tickets" level up. |
| 18.0.1.2.0 | The "Related tickets" tab added by `helpdesk_ticket_related` is labelled in French, "Billets liés", like the rest of the ticket form. |
| 18.0.1.1.0 | Refuses to merge tickets from different clients (the duplicate banner offers merging only for the same contact): link them to an incident instead. Company record rule on duplicate suggestions. Agents who are not administrators can merge. "Créer un problème" on a helpdesk theme links its recent tickets to a parent incident. The grouped incident reply that closes the children sends a single email per client. |
| 18.0.1.0.0 | First release: duplicate suggestions at creation, complete merge (timesheets, internal merge notices, trace on both sides, clean description, survey and reminders stopped on the merged ticket), parent incidents with grouped reply and optional closing. |
