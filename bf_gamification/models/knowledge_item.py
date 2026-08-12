import logging

from odoo import models

_logger = logging.getLogger(__name__)


class ProjectKnowledgeItem(models.Model):
    _inherit = 'project.knowledge.item'

    def write(self, vals):
        old_states = {rec.id: rec.state for rec in self}
        res = super().write(vals)
        if 'state' in vals:
            for rec in self:
                old = old_states.get(rec.id)
                if old != 'done' and rec.state == 'done':
                    self._award_knowledge_item_xp(rec)
        return res

    def _award_knowledge_item_xp(self, rec):
        if not self.env['ir.config_parameter'].sudo().get_param(
                'bf_gamification.gamification_enabled', 'True') == 'True':
            return
        try:
            user = rec.assigned_user_id or rec.create_uid
            if not user:
                return
            Profile = self.env['bf.gamification.profile']
            profile = Profile._get_or_create_profile(user)
            Rule = self.env['bf.gamification.xp.rule']
            rule = Rule.search([
                ('source', '=', 'knowledge_item'),
                ('trigger', '=', 'complete'),
                ('active', '=', True),
            ], limit=1)
            if rule:
                profile._award_xp(
                    rule.xp_amount, 'knowledge_item',
                    'Élément de matrice complété : %s' % (rec.name or ''),
                    reference=rec,
                )
        except Exception:
            _logger.warning("Fox Quest: erreur XP item matrice", exc_info=True)
