# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools import email_normalize, email_split

_logger = logging.getLogger(__name__)

# Filigrane : au premier passage on ne remonte pas plus loin que ça, pour ne
# pas rejouer des années d'archives sur une campagne qui vient de démarrer.
DEFAULT_LOOKBACK_DAYS = 7
PARAM_WATERMARK = "bf_outreach_email.last_scan"


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
    def _cron_match_inbound_emails(self):
        """Transforme en interactions les courriels reçus des cibles actives."""
        watermark = _read_watermark(self.env, PARAM_WATERMARK)
        since = (
            fields.Datetime.to_datetime(watermark)
            if watermark
            else fields.Datetime.now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        )

        emails = self.env["bf.email"].sudo().search(
            [("direction", "=", "in"), ("date", ">=", since)], order="date asc"
        )
        if not emails:
            _write_watermark(self.env, PARAM_WATERMARK, _now_string())
            return 0

        # Une seule requête pour toutes les adresses vues dans le lot.
        senders = {}
        for mail in emails:
            addresses = email_split(mail.email_from or "")
            normalized = email_normalize(addresses[0]) if addresses else False
            if normalized:
                senders.setdefault(normalized, []).append(mail)
        if not senders:
            _write_watermark(self.env, PARAM_WATERMARK, _now_string())
            return 0

        targets = self.search(
            [
                ("email_normalized", "in", list(senders)),
                ("campaign_state", "in", ("draft", "running")),
                ("stage_type", "not in", ("won", "lost")),
            ]
        )
        created = 0
        Touch = self.env["bf.outreach.touch"]
        for target in targets:
            for mail in senders.get(target.email_normalized, []):
                if Touch.search_count(
                    [("target_id", "=", target.id), ("bf_email_id", "=", mail.id)]
                ):
                    continue
                Touch.create(
                    {
                        "target_id": target.id,
                        "kind": "email",
                        "direction": "in",
                        "outcome": "replied",
                        "date": mail.date,
                        "summary": mail.subject or False,
                        "bf_email_id": mail.id,
                    }
                )
                created += 1
        _write_watermark(self.env, PARAM_WATERMARK, _now_string())
        if created:
            _logger.info("bf_outreach_email : %s réponse(s) rapprochée(s)", created)
        return created
