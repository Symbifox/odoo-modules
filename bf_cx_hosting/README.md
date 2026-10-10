# bf_cx_hosting: post-maintenance CSAT

Auto-installs when both `bf_cx` and `hosting_management` are installed.
When a scheduled maintenance touching a client service is marked done,
sends a 3-emoji feedback request (rating module) to the service's client.
Opt-in (`bf_cx.hosting_feedback`, off by default), with the solicitation
guardrails applied and internal partners excluded. Because schedules are
recurring, the sent flag is reset on each new occurrence: one possible
request per maintenance cycle, bounded by the anti-oversolicitation
guardrail. Branded email, in the contact's language, with an
unsubscribe link.

## Languages (v18.0.1.2.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The feedback email is written in English in the template and its French ships in the catalogue. It is rendered in the contact's language, or the company's when the contact has none. On upgrade, a template that still holds the delivered French switches its English source; a template edited by hand is left as it is.
