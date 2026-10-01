"""Solicitation guard on the bf_helpdesk closing CSAT.

bf_helpdesk (when present) emails a CSAT survey at ticket closure through
its own _send_csat_invite() - outside the bf_cx guards. This override adds
the cooldown/DNC check and stamps the solicitation. Defensive: this bridge
only depends on helpdesk_mgmt, so on a tenant without bf_helpdesk the
method simply has no super and no caller.
"""
import logging

from odoo import _, models

from odoo.addons.bf_cx.models.bf_cx_feedback import param_is_true

_logger = logging.getLogger(__name__)


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    def _send_csat_invite(self):
        # bf_helpdesk peut se charger avant ou après ce pont (aucune
        # dépendance entre les deux). Au-dessus de nous, il nous appelle ; au-
        # dessous, il est notre parent. Dans les deux cas, l'envoi réel est
        # _bf_csat_deliver. Sans bf_helpdesk, il n'y a rien à garder.
        parent = super()
        if hasattr(parent, "_send_csat_invite"):
            deliver = parent._send_csat_invite
        elif hasattr(self, "_bf_csat_deliver"):
            deliver = self._bf_csat_deliver
        else:
            return None
        if param_is_true(self.env, "bf_cx.guard_rating_requests", default=True):
            partner = self.partner_id
            if partner:
                allowed, blocked = partner._bf_cx_split_solicitable()
                if blocked:
                    self.message_post(
                        body=_(
                            "Sondage CSAT non envoyé à %s : garde-fou de "
                            "sollicitation (cooldown, liste à ne pas "
                            "contacter ou dossier en recouvrement)."
                        )
                        % partner.display_name
                    )
                    return None
                partner._bf_cx_mark_solicited()
        return deliver()
