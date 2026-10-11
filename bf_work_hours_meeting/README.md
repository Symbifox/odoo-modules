# BF Working Hours - Meetings (`bf_work_hours_meeting`)

Bridge between **BF Working Hours** and **BF Meetings**, installed automatically when both are.

A meeting report where the person is marked *present* counts as a meeting in their working
time, from its start for its length (under 8 hours). Not what was said.

The presence counts only if it was noted by the person, by Odoo, or by an account of the meeting
processing named in the system parameter `bf_work_hours_meeting.processing_logins`
(comma-separated logins): a colleague who can write meeting reports cannot mark someone present.
Each presence is stamped when it is written, with the meeting's hours at that moment: a meeting
a colleague stretches afterwards does not stretch the presence (the person or the processing
refining the report carries it to the new hours). A report without attendance counts for nobody.

**Set `bf_work_hours_meeting.processing_logins` before installing**: the presences noted before
the installation are stamped then, from their last writer. Set later, it trusts every presence the
named accounts noted since the installation (each stamp keeps the account that noted it), and
that cannot be undone by changing the parameter back: **name only the accounts of the meeting
processing**, never a person nor an assistant that colleagues can instruct.

A meeting also in the calendar counts once: stretches are merged, a minute counts once.
