from odoo import api, fields, models


class GrantBadgeWizard(models.TransientModel):
    _name = 'bf.gamification.grant.badge.wizard'
    _description = 'Attribuer un badge'

    user_ids = fields.Many2many('res.users', string="Utilisateurs", required=True)
    badge_id = fields.Many2one('bf.gamification.badge', string="Badge", required=True)
    note = fields.Text(string="Message personnalis\u00e9")
    trigger_popup = fields.Boolean(string="D\u00e9clencher l'animation", default=True)

    def action_grant(self):
        """Grant the selected badge to all selected users."""
        Profile = self.env['bf.gamification.profile']
        for user in self.user_ids:
            profile = Profile._get_or_create_profile(user)
            profile._grant_badge(
                self.badge_id,
                granted_by=self.env.user,
                note=self.note,
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Badge attribu\u00e9',
                'message': '%s attribu\u00e9 \u00e0 %d utilisateur(s)' % (
                    self.badge_id.name, len(self.user_ids)),
                'type': 'success',
                'sticky': False,
            },
        }
