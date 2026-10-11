# BF Working Hours - Timer (`bf_work_hours_timer`)

Bridge between **BF Working Hours** and the timesheet timer, installed automatically when both are.

The timer folds each stretch it runs into a total, and the timesheet line it creates keeps only
a date and a duration: the time of day is lost. For the people who turned *Propose my hours* on,
each stretch is kept with its start and end, at the pause and at the stop. A timer still
running does not count yet, and a stretch of more than 12 hours is a forgotten timer: it is not
kept at all. Not the task.

A timer whose timing (start, pause, stop) someone other than its owner changes, an approver for
instance, no longer counts: it no longer says when its owner worked. Nor does a timer someone made
for another person, handed over to them, or copied from such a timer.
