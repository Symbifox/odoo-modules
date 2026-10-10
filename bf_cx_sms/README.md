# bf_cx_sms: survey invitations by SMS

Auto-installs when both `bf_cx` and `bf_sms_archive` are installed. Adds an
"Invite by SMS" button on send waves: recipients with no email address but
a phone number receive their personal survey link (individual token) by
SMS, on the configured line. Opt-in (`bf_cx.sms_invite`, off by default),
manual action only (no cron), a maximum of 5 texts per click, with the
solicitation guardrails applied.

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. Each SMS invitation is written in its recipient's language.
