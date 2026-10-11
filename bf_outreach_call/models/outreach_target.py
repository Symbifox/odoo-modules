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


def _read_watermark(env, key):
    """Filigrane lu en SQL, hors du cache de get_param."""
    env["ir.config_parameter"].flush_model(["key", "value"])
    env.cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", [key])
    row = env.cr.fetchone()
    return row[0] if row else False


def _write_watermark(env, key, value):
    """Filigrane écrit SANS vider le cache de tous les processus.

    ``set_param`` appelle ``registry.clear_cache()`` : chaque écriture refroidissait
    tous les workers, dont la page suivante coûtait alors 1,7 s de plus. Ce
    filigrane n'est lu que par sa tâche, en SQL : aucun cache ne peut le servir
    périmé.
    """
    env["ir.config_parameter"].flush_model(["key", "value"])
    env.cr.execute(
        """
        INSERT INTO ir_config_parameter (key, value, create_uid, create_date, write_uid, write_date)
        VALUES (%(key)s, %(value)s, %(uid)s, now() AT TIME ZONE 'UTC',
                %(uid)s, now() AT TIME ZONE 'UTC')
        ON CONFLICT (key) DO UPDATE
           SET value = EXCLUDED.value,
               write_uid = EXCLUDED.write_uid,
               write_date = EXCLUDED.write_date
        """,
        {"key": key, "value": value, "uid": env.uid},
    )
    env["ir.config_parameter"].invalidate_model(["value"])


def _now_string():
    return fields.Datetime.to_string(fields.Datetime.now())


class BfOutreachTarget(models.Model):
    _inherit = "bf.outreach.target"

    @api.model
    def _cron_match_archived_calls(self):
        """Transforme en interactions les appels réellement passés aux cibles."""
        watermark = _read_watermark(self.env, PARAM_WATERMARK)
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
            _write_watermark(self.env, PARAM_WATERMARK, _now_string())
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
        _write_watermark(self.env, PARAM_WATERMARK, _now_string())
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
