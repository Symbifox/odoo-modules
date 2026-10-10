# bf_cx_sign: post-signature feedback

Auto-installs when both `bf_cx` and `bf_sign` are installed. When a
signature request is completed (document sealed), sends a 3-emoji feedback
request (rating module) to the main signer. Opt-in
(`bf_cx.sign_feedback`, off by default), one request per signature
request, with the solicitation guardrails applied.

## Languages (v18.0.1.2.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The feedback email is written in English in the template and its French ships in the catalogue. It is rendered in the contact's language, or the company's when the contact has none. On upgrade, a template that still holds the delivered French switches its English source; a template edited by hand is left as it is. The chatter note left when the contact is in cooldown is written in the language of the request's sender, not in the signer's.
