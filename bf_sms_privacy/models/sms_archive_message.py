"""Le message SMS/MMS sous règle TEL-001 — trois ans, alignés sur COR-001."""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class SmsArchiveMessage(models.Model):
    _name = "sms.archive.message"
    _inherit = ["sms.archive.message", "bf.sms.privacy.regime"]

    _privacy_date_field = "date_sent"
    _privacy_hash_field = "message_hash"
    _privacy_rule_xmlid = "bf_sms_privacy.retention_sms_message"
    _privacy_campaign_label = "Messagerie SMS/MMS"

    @api.model
    def _cron_privacy_propose_destruction(self, limit=None, dry_run=False):
        """Le cron du pont : balaie les DEUX gisements, ne détruit rien.

        Un seul cron pour les messages et les appels. Deux crons donneraient
        deux horaires à tenir d'accord et deux journaux à recouper, pour une
        passe qui coûte moins d'une seconde.
        """
        if limit is None:
            ICP = self.env["ir.config_parameter"].sudo()
            try:
                limit = int(ICP.get_param("bf_sms_privacy.batch_size", "500"))
            except (TypeError, ValueError):
                limit = 500

        proposed = self._privacy_propose_expired(limit=limit, dry_run=dry_run)
        proposed += self.env["call.archive.call"]._privacy_propose_expired(
            limit=limit, dry_run=dry_run,
        )
        _logger.info(
            "bf_sms_privacy: %s ligne(s) proposée(s) à la destruction%s",
            proposed, " (simulation)" if dry_run else "",
        )
        return proposed
