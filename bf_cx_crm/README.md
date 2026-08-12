# bf_cx_crm - post-loss survey

Auto-installs when both `bf_cx` and `crm` are installed. When an
opportunity is marked lost, sends the designated program's survey
(`bf_cx.loss_program_id`, empty = disabled) to the contact: once per
opportunity, with the solicitation guardrails applied and the loss reason
recorded in the chatter.
