from odoo import api, models
from odoo.exceptions import UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def bf_avatar_unlock(self, reward_id):
        """Buy an avatar pack for the caller, with the caller's own XP."""
        avatars = self.env["bf.avatar"]
        style = avatars._check_character_style()
        reward = self.env["bf.gamification.reward"].sudo().browse(int(reward_id)).exists()
        if not reward or reward not in avatars._avatar_packs(style):
            raise UserError(self.env._("This avatar pack does not exist."))
        # Created as the caller: Fox Quest's own rules (access, balance, stock,
        # one per person) apply unchanged.
        self.env["bf.gamification.reward.claim"].create({
            "user_id": self.env.user.id, "reward_id": reward.id})
        return self.bf_avatar_composer()
