# bf_cx_helpdesk - Customer Experience to Helpdesk bridge

Auto-installs when both `bf_cx` and `helpdesk_mgmt` (OCA) are installed.

- A "Complaints" helpdesk team and a "Customer experience" channel (data).
- A ticket from a complaint (the complaint keeps the link to its ticket) and a follow-up ticket from
  detractor feedback; the automatic ticket is opt-in
  (`bf_cx.auto_ticket`, off by default).
- The solicitation guardrail is applied to bf_helpdesk's closing CSAT
  survey when that module is present (defensive: no dependency on
  bf_helpdesk).

## Languages (18.0.1.2.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. A ticket opened from a complaint or from detractor feedback is named and described in the customer's language (the contact's, else their company's, else the issuing company's), since it can reach the customer. The note left on the complaint follows the user who clicks. The note saying a closing satisfaction survey was not sent follows the ticket's assignee, else the language it was triggered in. On upgrade, the shipped channel's name switches to English only where the database still holds the name that was shipped.

## Changelog

| Version | Change |
|---|---|
| 18.0.1.2.0 | Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Tickets from complaints and detractor feedback are named and described in the customer's language, and the unsent survey note in the assignee's. See the Languages section. |
| 18.0.1.1.1 | The closing satisfaction survey of `bf_helpdesk` is sent again in every module load order: when `bf_helpdesk` loaded after this bridge, the guardrail check returned without sending anything. The guardrail itself is unchanged. |
