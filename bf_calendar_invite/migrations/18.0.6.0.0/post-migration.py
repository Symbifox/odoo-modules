"""Start the ledger level, so nothing is announced about the past.

`bf_ics_sequence_notified` arrives with a default of 0, and a field default is
written into every existing row when its column is created. On a real calendar
that would declare every meeting ever revised as one the guests were never told
about: true, in the letter, and useless. A few of them are recent, fewer still
are in the future, and the oldest changes are months old.

So the ledger starts level: what was counted is declared announced. The
meetings that genuinely owe someone a word are a decision for a human, taken
once, after reading the list. Announcing them by running a
migration would send mail about changes made days ago, to clients, with nobody
having read the message first.

⚠️ The baseline is cleared with it. A "before" column captured from a change
that predates the notice would describe a version of the meeting no guest was
ever told about.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE calendar_event
           SET bf_ics_sequence_notified = COALESCE(bf_ics_sequence, 0),
               bf_change_baseline = NULL
         WHERE COALESCE(bf_ics_sequence_notified, 0)
               IS DISTINCT FROM COALESCE(bf_ics_sequence, 0)
            OR bf_change_baseline IS NOT NULL
        """
    )
