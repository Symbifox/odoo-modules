# bf_cx_helpdesk - Customer Experience to Helpdesk bridge

Auto-installs when both `bf_cx` and `helpdesk_mgmt` (OCA) are installed.

- A "Complaints" helpdesk team and a "Customer experience" channel (data).
- A ticket from a complaint (two-way link) and a follow-up ticket from
  detractor feedback; the automatic ticket is opt-in
  (`bf_cx.auto_ticket`, off by default).
- The solicitation guardrail is applied to bf_helpdesk's closing CSAT
  survey when that module is present (defensive: no dependency on
  bf_helpdesk).
