# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 7
PARAM_WATERMARK = "bf_outreach_call.last_scan"

# Ce que chaque type d'appel de l'archive veut dire pour une campagne :
# (sens, résultat lorsque personne n'a décroché, résultat lorsqu'on a parlé).
CALL_TYPE_MAP = {
    "outgoing": ("out", "no_answer", "reached"),
    "incoming": ("in", "no_answer", "reached"),
    "missed": ("in", "no_answer", "no_answer"),
    "voicemail": ("in", "voicemail", "voicemail"),
    # « rejeté » et « bloqué » ne disent rien de la campagne : on les ignore.
}


class BfOutreachTarget(models.Model):
    _inherit = "bf.outreach.target"

    @api.model
    def _cron_match_archived_calls(self):
        """Transforme en interactions les appels réellement passés aux cibles."""
        params = self.env["ir.config_parameter"].sudo()
        watermark = params.get_param(PARAM_WATERMARK)
        since = (
            fields.Datetime.to_datetime(watermark)
            if watermark
            else fields.Datetime.now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        )
        calls = self.env["call.archive.call"].sudo().search(
            [("date", ">=", since), ("call_type", "in", list(CALL_TYPE_MAP))],
            order="date asc",
        )
        if not calls:
            params.set_param(
                PARAM_WATERMARK, fields.Datetime.to_string(fields.Datetime.now())
            )
            return 0

        by_phone = {}
        for call in calls:
            number = call.thread_id.phone_normalized
            if number:
                by_phone.setdefault(number, []).append(call)
        created = 0
        if by_phone:
            targets = self.search(
                [
                    ("phone_normalized", "in", list(by_phone)),
                    ("campaign_state", "in", ("draft", "running")),
                    ("stage_type", "not in", ("won", "lost")),
                ]
            )
            Touch = self.env["bf.outreach.touch"]
            for target in targets:
                for call in by_phone.get(target.phone_normalized, []):
                    if Touch.search_count(
                        [("target_id", "=", target.id), ("call_archive_id", "=", call.id)]
                    ):
                        continue
                    Touch.create(target._call_touch_values(call))
                    created += 1
        params.set_param(
            PARAM_WATERMARK, fields.Datetime.to_string(fields.Datetime.now())
        )
        if created:
            _logger.info("bf_outreach_call : %s appel(s) rapproché(s)", created)
        return created

    def _call_touch_values(self, call):
        """Traduit un appel archivé en interaction de démarchage."""
        self.ensure_one()
        direction, outcome_silent, outcome_spoken = CALL_TYPE_MAP[call.call_type]
        # Une durée nulle veut dire que personne n'a décroché, quel que soit le type.
        outcome = outcome_spoken if (call.duration or 0) > 0 else outcome_silent
        owner = call.owner_id or self.user_id or self.env.user
        return {
            "target_id": self.id,
            "kind": "call",
            "direction": direction,
            "outcome": outcome,
            "date": call.date,
            # L'archive compte en secondes, l'interaction en minutes.
            "duration": round((call.duration or 0) / 60.0, 2),
            "call_archive_id": call.id,
            "user_id": owner.id,
        }
