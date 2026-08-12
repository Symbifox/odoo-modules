# bf_cx_meeting: post-report feedback

Auto-installs when both `bf_cx` and `bf_meeting` are installed. When a
meeting report is sent to the client, sends a 3-emoji feedback request
(rating module) to the project partner. Opt-in
(`bf_cx.meeting_feedback`, off by default) with the solicitation
guardrails applied: the per-contact anti-oversolicitation cooldown, plus a
per-meeting flag (`bf_cx_feedback_requested`) so that resending a report
does not trigger a second request.
