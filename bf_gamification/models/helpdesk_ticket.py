import logging

from odoo import models

_logger = logging.getLogger(__name__)


class HelpdeskTicket(models.Model):
    _inherit = 'helpdesk.ticket'

    def write(self, vals):
        old_stages = {ticket.id: ticket.stage_id for ticket in self}
        res = super().write(vals)
        if 'stage_id' in vals:
            for ticket in self:
                self._check_ticket_resolution_xp(ticket, old_stages.get(ticket.id))
        return res

    def _check_ticket_resolution_xp(self, ticket, old_stage):
        """Award XP when a helpdesk ticket is moved to a resolved stage."""
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return

        try:
            new_stage = ticket.stage_id
            if not new_stage or not new_stage.fold:
                return
            if old_stage and old_stage.fold:
                return  # Already in a closed stage

            user = ticket.user_id or ticket.create_uid
            if not user:
                return

            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(user)
            Rule = self.env['bf.gamification.xp.rule']

            rule = Rule.search([
                ('source', '=', 'helpdesk'),
                ('trigger', '=', 'complete'),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, 'helpdesk',
                    'Ticket r\u00e9solu : %s' % ticket.name,
                    reference=ticket,
                )
        except Exception:
            _logger.warning("Fox Quest: erreur XP ticket", exc_info=True)
