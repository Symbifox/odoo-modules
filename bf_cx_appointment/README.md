# bf_cx_appointment: post-appointment feedback

Auto-installs when both `bf_cx` and `bf_appointment` are installed. When
an appointment has finished (a pass on the appointment email cron), sends
a 3-emoji feedback request (rating module) to the appointment contact.
Opt-in (`bf_cx.appointment_feedback`, off by default), one request per
booking, recent bookings only, and the solicitation guardrails apply.
