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


class BfOutreachTarget(models.Model):
    _inherit = "bf.outreach.target"

    @api.model
    def _cron_match_inbound_emails(self):
        """Transforme en interactions les courriels reçus des cibles actives."""
        params = self.env["ir.config_parameter"].sudo()
        watermark = params.get_param(PARAM_WATERMARK)
        since = (
            fields.Datetime.to_datetime(watermark)
            if watermark
            else fields.Datetime.now() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        )

        emails = self.env["bf.email"].sudo().search(
            [("direction", "=", "in"), ("date", ">=", since)], order="date asc"
        )
        if not emails:
            params.set_param(PARAM_WATERMARK, fields.Datetime.to_string(fields.Datetime.now()))
            return 0

        # Une seule requête pour toutes les adresses vues dans le lot.
        senders = {}
        for mail in emails:
            addresses = email_split(mail.email_from or "")
            normalized = email_normalize(addresses[0]) if addresses else False
            if normalized:
                senders.setdefault(normalized, []).append(mail)
        if not senders:
            params.set_param(PARAM_WATERMARK, fields.Datetime.to_string(fields.Datetime.now()))
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
        params.set_param(
            PARAM_WATERMARK, fields.Datetime.to_string(fields.Datetime.now())
        )
        if created:
            _logger.info("bf_outreach_email : %s réponse(s) rapprochée(s)", created)
        return created
