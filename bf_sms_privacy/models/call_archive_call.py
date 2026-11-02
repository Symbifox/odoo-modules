"""Le journal d'appel : TEL-002 par défaut, TEL-003 s'il est versé à une rencontre.

⚠️ **Pourquoi deux régimes ici et un seul côté messages.** Des comptes rendus
portent un `call_archive_id` — le lien posé par
`bf_meeting_call_archive`. Un compte rendu se conserve six ans (RENC-001,
cinq ans plus une année semi-active) ; laisser l'appel mourir à trois ans
viderait en silence le renvoi de comptes rendus encore sous conservation.
Le Many2one est `ondelete` par défaut, donc rien ne casserait : le compte rendu
perdrait sa pièce sans que personne ne le voie passer. C'est exactement le
genre de perte qu'un calendrier de conservation est censé empêcher.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CallArchiveCall(models.Model):
    _name = "call.archive.call"
    _inherit = ["call.archive.call", "bf.sms.privacy.regime"]

    _privacy_date_field = "date"
    _privacy_hash_field = "call_hash"
    _privacy_rule_xmlid = "bf_sms_privacy.retention_call_log"
    _privacy_campaign_label = "Journaux d'appels"

    # ------------------------------------------------------------------
    # Le second régime
    # ------------------------------------------------------------------

    @api.model
    def _privacy_meeting_rule(self):
        """TEL-003 — l'appel versé à un compte rendu."""
        return self.env.ref(
            "bf_sms_privacy.retention_call_meeting", raise_if_not_found=False,
        )

    @api.model
    def _privacy_rules(self):
        rules = super()._privacy_rules()
        meeting_rule = self._privacy_meeting_rule()
        if meeting_rule:
            rules |= meeting_rule
        return rules

    def _privacy_linked_meetings(self):
        """Comptes rendus qui pointent vers cet appel — ARCHIVÉS COMPRIS.

        ⚠️ `meeting.record` porte un champ `active`. Lire sans
        `active_test=False` ferait passer un compte rendu archivé pour absent,
        et l'appel repasserait sous les trois ans alors que le compte rendu
        qui le cite est encore là, conservé, consultable en cochant
        « Archivé » : ranger n'est pas clore.

        Rend `None` quand `bf_meeting_call_archive` n'est pas installé : il n'y
        a alors pas de lien à respecter, et tout tombe sous TEL-002.
        """
        self.ensure_one()
        if "meeting_record_ids" not in self._fields:
            return None
        return self.sudo().with_context(active_test=False).meeting_record_ids

    def _privacy_anchor(self):
        self.ensure_one()
        meetings = self._privacy_linked_meetings()
        if not meetings:
            return super()._privacy_anchor()
        rule = self._privacy_meeting_rule()
        dates = [record.date for record in meetings if record.date]
        if not dates:
            return False, rule, _("appel versé à un compte rendu sans date")
        # Le plus RÉCENT : un appel cité par deux comptes rendus se conserve
        # aussi longtemps que le dernier d'entre eux.
        return fields.Date.to_date(max(dates)), rule, False

    # ------------------------------------------------------------------
    # Balayage
    # ------------------------------------------------------------------

    @api.model
    def _privacy_scan_expired(self, limit=None):
        held = defaultdict(int)
        buckets = defaultdict(lambda: self.browse())

        rule = self._privacy_rule()
        days = self._privacy_rule_days(rule)
        if not days:
            held[_("règle TEL-002 absente ou sans durée")] += (
                self.sudo().search_count([])
            )
            return dict(buckets), dict(held)

        meeting_rule = self._privacy_meeting_rule()
        meeting_days = self._privacy_rule_days(meeting_rule)

        today = fields.Date.context_today(self)
        # Personne ne peut être échu avant TEL-002 : TEL-003 est plus longue.
        # On ne charge donc que ce qui a déjà franchi la plus courte des deux.
        rows = self.sudo().search(
            [("date", "<", today - timedelta(days=days))],
            order="date asc", limit=limit or None,
        )

        for call in rows:
            meetings = call._privacy_linked_meetings()
            if not meetings:
                buckets[rule.id] |= call
                continue
            if not meeting_days:
                held[_("appel versé à un compte rendu : règle TEL-003 absente "
                       "ou sans durée")] += 1
                continue
            anchor, _rule_row, motif = call._privacy_anchor()
            if not anchor:
                held[motif or _("ancrage indéterminé")] += 1
                continue
            if anchor + timedelta(days=meeting_days) > today:
                held[_("appel versé à un compte rendu encore sous "
                       "conservation (TEL-003)")] += 1
                continue
            buckets[meeting_rule.id] |= call
        return dict(buckets), dict(held)
