# Meetings — Client portal (`bf_meeting_portal`)

Gives the client read access, inside their Odoo portal, to the
[`bf_meeting`](../bf_meeting) meeting reports **that were already emailed to
them**, under `/my/meetings`.

## Use case

The client received a report by email three weeks ago and cannot find it any
more. Rather than resending it, they look it up themselves.

## Principle: an archive, not a disclosure

The portal publishes nothing new. A report only appears there when:

- `report_state == 'sent'`,
- `report_sent_date` is set, **and**
- the partner is in `report_recipient_ids`.

⚠️ **State alone proves nothing.** `report_state` is an ordinary field: the
`bf_meeting` kanban view is grouped on it without `records_draggable="0"`, so
dragging a card into the "Sent" column emits a bare `write`; import scripts and
XML-RPC writes set it directly too. On the originating database, 118 of the 202
"sent" reports had no `report_sent_date` at all.

`report_sent_date`, on the other hand, is only written by
`action_send_report_direct`, in the same `write()` as the state, and that method
refuses to run without recipients and queues an email. Requiring it makes the
invariant true instead of promised — and on the original data it removed no
legitimate report.

⚠️ **Integration caveat.** This invariant holds as long as nothing writes
`report_sent_date` raw. If you have scripts, automations or external tools
manipulating `meeting.record` through the ORM or XML-RPC, have them call
`action_send_report_direct` rather than writing the fields directly. The field's
`readonly=True` offers no protection: it is a view constraint, and the ORM and
XML-RPC write anyway.

The partner considered is the portal user **or their company**
(`commercial_partner_id`): contacts within one organisation see what was
addressed to the organisation, but not what was addressed to a colleague by
name.

⚠️ **The criterion is not "attendee".** Sending a report in `bf_meeting`
deliberately has no fallback on the attendee list, to avoid accidental sends to
the client. Wiring portal visibility to attendance would reopen exactly that
risk: someone attends a meeting and could read a report nobody chose to send
them.

## Agendas are deliberately not exposed

They were, in 1.x. They were removed in 2.0.0, because **no field in
`bf_meeting` proves that an agenda was ever sent.**

- `sent_date` does not prove it. `action_send_agenda_wizard` stamps it on merely
  **opening** the composer, in order to open the contributions window, and
  nothing clears it if the wizard is abandoned.
- Nor does the state. `action_confirm()` only sends when `auto_send_on_confirm`
  is set, and its default is `False`; `action_start_meeting()` confirms a draft
  automatically; `action_create_meeting_record()` writes `done`
  unconditionally. So `confirmed`/`done` is reachable without a single email.
- Nor does the chatter: `mail.mail` records are purged after sending, and on
  real data neither `message_type`, nor `notification_ids`, nor `partner_ids`
  distinguished a send from an abandonment.

Together, those three points made an internal, never-sent agenda visible to an
attending client — and since a draft rarely has `recipient_ids`, it surfaced
through the widest fallback branch, `participant_ids`. Rather than inventing an
approximate criterion over confidential content, agendas are out of the portal.
They will return once `bf_meeting` carries a reliable sent marker.

## What is exposed, and what never is

Displayed: summary, topics and their points, decisions (with decision-maker and
linked item), action items (assignee, deadline), open questions, deliverables,
attendance. The page draws these sections from
`meeting.record._get_report_data()`, the method feeding the client report, so it
stays at parity with it.

**PDF**: the report attached to the meeting report email
(`report_template_ids` → `action_report_meeting_record`). It is **regenerated on
read**, not taken from the archived attachment, so it reflects corrections made
to the report since it was sent. Same report, not the same bytes.

**Never passed to the template**: `verbatim`, `verbatim_html` (raw
transcription), `review_notes`, or the live notes. `structured_notes_json` is
not exposed as such; only the sections the client report already renders are
extracted from it. Attachments are not exposed.

## Security

**No ORM rights are granted to the portal group** — neither `ir.model.access`
nor `ir.rule`. That is deliberate: granting ACL on `meeting.record` would open
`/web/dataset/call_kw` and let an authenticated portal user read the raw
transcription over RPC, outside the templates.

The controller is therefore the only door. It applies the visibility domain
inside the search itself — `search([('id','=',id)] + domain)`, never a
`browse()` on a client-supplied id — then switches to `sudo()` to read the data.
Templates receive only **whitelisted dictionaries**, never the record: an
internal field stays unreachable even if a template is carelessly edited later.

`report_state`, `report_sent_date` and `report_recipient_ids` are set
`copy=False` (see `models/meeting_record.py`). Without that, duplicating a sent
report produced a copy marked "sent", recipients intact, which the portal would
have shown even though no email ever went out for it.

All routes are `auth='user'`. Archived records are excluded.

## Structure

```
controllers/portal.py                 visibility domain, whitelists, routes
models/meeting_record.py              copy=False on the visibility fields
views/meeting_portal_templates.xml    home entry, breadcrumb, list, detail
static/src/img/portal_icon.svg        card icon (64x64 intrinsic)
```

Routes: `/my/meetings`, `/my/meetings/record/<id>` and
`/my/meetings/record/<id>/pdf`.

The home entry declares `placeholder_count`: `portal.portal_docs_entry` renders
its cards `d-none` by default, so a card with no counter stays invisible. The
counter is fed by `_prepare_home_portal_values`.

## Dependencies

- `bf_meeting` — the `meeting.record` model
- `portal` — `CustomerPortal` and the home templates

## Installation

```bash
odoo -d <database> -i bf_meeting_portal --stop-after-init
```

Since the module declares controllers, restart the service afterwards so the
routes are served.

No configuration is required. The scope depends entirely on
`report_recipient_ids`: on a database whose history was sent without populating
that field, the portal will only cover future sends.

## Licence

LGPL-3
