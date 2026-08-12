# Symbifox — Operations Pack

Bundle meta-module for consulting/MSP operations on Odoo 18 CE. Brings together the unified inbox, meeting workflow, contact intelligence, helpdesk fork, hour-bank billing, and chatter polish layer.

## Included modules

### Inbox & messaging
| Module | Role |
|---|---|
| [`bf_email_management`](../bf_email_management) | IMAP unified inbox + chatter projection |
| [`bf_mail_import`](../bf_mail_import) | Drop `.eml` files into the chatter |
| [`bf_mail_subject_clean`](../bf_mail_subject_clean) | Prevent `Re: Re: Re:` stacking |
| [`bf_mail_vigie`](../bf_mail_vigie) | Re-route misrouted chatter emails |
| [`bf_chatter_chronological`](../bf_chatter_chronological) | Sort chatter by email Date header |
| [`bf_chatter_timesheet`](../bf_chatter_timesheet) | Log a timesheet entry from the chatter composer |

### Meetings, contacts, helpdesk, billing
| Module | Role |
|---|---|
| [`bf_meeting`](../bf_meeting) | Agendas + meeting records around `calendar.event` |
| [`bf_persona`](../bf_persona) | Relationship intelligence, auto-cc, weekly degradation |
| [`bf_helpdesk`](../bf_helpdesk) | Branded helpdesk fork with hour-bank ribbon and SLA |
| [`bf_hour_bank`](../bf_hour_bank) | Hour-bank tracking and billing |

Installing `bf_ops_pack` installs all of the above. Uninstalling it does **not** cascade.

> All bundled modules are self-contained and depend only on Odoo CE and other public Symbifox modules in this repo. `bf_helpdesk`'s optional AI triage reads an Anthropic key via `ir.config_parameter` and degrades gracefully when unconfigured, so the bundle installs without any private/proprietary dependency.

## License

GNU LGPL-3. See [`LICENSE`](LICENSE) for the full text.
