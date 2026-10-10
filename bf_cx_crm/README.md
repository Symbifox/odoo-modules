# bf_cx_crm - post-loss survey

Auto-installs when both `bf_cx` and `crm` are installed. When an
opportunity is marked lost, sends the designated program's survey
(`bf_cx.loss_program_id`, empty = disabled) to the contact: once per
opportunity, with the solicitation guardrails applied and the loss reason
recorded in the chatter.

## Languages (v18.0.1.2.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English.
