from odoo import api, models

from .gamification_reward import parse_parts

OWNED_STATES = ("approved", "consumed")


class BfAvatar(models.AbstractModel):
    _inherit = "bf.avatar"

    @api.model
    def _avatar_packs(self, style):
        return self.env["bf.gamification.reward"].sudo().search([
            ("avatar_style", "=", style), ("avatar_parts", "!=", False),
            ("active", "=", True)], order="xp_cost asc, id asc")

    @api.model
    def _locked_parts(self, user, style):
        locked = super()._locked_parts(user, style)
        packs = self._avatar_packs(style)
        if not packs or not user:
            return locked
        owned = self.env["bf.gamification.reward.claim"].sudo().search([
            ("user_id", "=", user.id), ("reward_id", "in", packs.ids),
            ("state", "in", OWNED_STATES)]).reward_id
        # A part sold in a pack the person owns is theirs, even if a dearer
        # pack sells it too.
        free = set()
        for pack in packs & owned:
            free |= parse_parts(pack.avatar_parts)
        balance = self.env["bf.gamification.profile"].sudo()._get_or_create_profile(user).xp_balance
        for pack in packs - owned:
            for part in parse_parts(pack.avatar_parts):
                if part in free or part in locked:
                    continue
                locked[part] = {
                    "reward_id": pack.id,
                    "name": pack.name,
                    "cost": pack.xp_cost,
                    "affordable": pack.available and balance >= pack.xp_cost,
                }
        return locked

    @api.model
    def _composer_extra(self, user, style):
        extra = super()._composer_extra(user, style)
        extra["xp_balance"] = self.env["bf.gamification.profile"].sudo()._get_or_create_profile(user).xp_balance
        return extra
