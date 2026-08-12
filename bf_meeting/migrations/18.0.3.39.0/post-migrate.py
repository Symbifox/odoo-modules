import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Backfill the new stored columns on existing rows.

    Field defaults only fire on new ``create()``, so existing agendas/topics
    would otherwise read NULL on the freshly-added stored columns, which can
    confuse views and ``ir.rule`` domains.

    Notes
    -----
    * ``access_token`` is minted lazily at *send* time, so we deliberately do
      NOT backfill tokens for historical agendas — that keeps the public
      exposure surface as small as possible.
    * ``contributions_open`` is a stored compute; it will recompute on the next
      relevant write, but we set it explicitly so the column is never NULL
      between the upgrade and the first write.
    """
    # meeting.agenda: closed by default until (re)sent while draft.
    cr.execute(
        "UPDATE meeting_agenda SET contributions_open = FALSE "
        "WHERE contributions_open IS NULL"
    )
    cr.execute(
        "UPDATE meeting_agenda SET allow_contributions = TRUE "
        "WHERE allow_contributions IS NULL"
    )

    # meeting.agenda.topic: pre-existing topics are staff-authored & accepted.
    cr.execute(
        "UPDATE meeting_agenda_topic SET source = 'staff' "
        "WHERE source IS NULL"
    )
    cr.execute(
        "UPDATE meeting_agenda_topic SET moderation_state = 'accepted' "
        "WHERE moderation_state IS NULL"
    )
    _logger.info("bf_meeting 18.0.3.39.0: contribution columns backfilled")
