# bf_cx_portal: feedback in the client portal

Auto-installs when both `bf_cx` and `portal` are installed. Adds the
`/my/feedback` portal page: the signed-in client reviews their company's
feedback (date, type, score, comment; internal 360 feedback is excluded)
and can submit a free-text comment, recorded as a verbatim ("Other"
channel) in the unified register. No outbound messages. Reads are bounded
to the user's commercial partner (strict-domain controller plus read-only
ACL plus a portal record rule); on create, only the comment text comes
from the user, everything else is forced server-side.

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English.
