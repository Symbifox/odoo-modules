# bf_cx_helpdesk - Customer Experience to Helpdesk bridge

Auto-installs when both `bf_cx` and `helpdesk_mgmt` (OCA) are installed.

- A "Complaints" helpdesk team and a "Customer experience" channel (data).
- A ticket from a complaint (the complaint keeps the link to its ticket) and a follow-up ticket from
  detractor feedback; the automatic ticket is opt-in
  (`bf_cx.auto_ticket`, off by default).
- The solicitation guardrail is applied to bf_helpdesk's closing CSAT
  survey when that module is present (defensive: no dependency on
  bf_helpdesk).

## Changelog

| Version | Change |
|---|---|
| 18.0.1.1.1 | The closing satisfaction survey of `bf_helpdesk` is sent again in every module load order: when `bf_helpdesk` loaded after this bridge, the guardrail check returned without sending anything. The guardrail itself is unchanged. |
