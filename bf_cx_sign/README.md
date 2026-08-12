# bf_cx_sign: post-signature feedback

Auto-installs when both `bf_cx` and `bf_sign` are installed. When a
signature request is completed (document sealed), sends a 3-emoji feedback
request (rating module) to the main signer. Opt-in
(`bf_cx.sign_feedback`, off by default), one request per signature
request, with the solicitation guardrails applied.
