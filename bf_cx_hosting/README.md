# bf_cx_hosting: post-maintenance CSAT

Auto-installs when both `bf_cx` and `hosting_management` are installed.
When a scheduled maintenance touching a client service is marked done,
sends a 3-emoji feedback request (rating module) to the service's client.
Opt-in (`bf_cx.hosting_feedback`, off by default), with the solicitation
guardrails applied and internal partners excluded. Because schedules are
recurring, the sent flag is reset on each new occurrence: one possible
request per maintenance cycle, bounded by the anti-oversolicitation
guardrail. Branded bilingual email (shared i18n hook from `bf_cx`) with an
unsubscribe link.
