from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.bf_avatar.lib import render


def parse_parts(text):
    """"slot:variant" pairs, one per line (commas and spaces also separate)."""
    parts = set()
    for token in (text or "").replace(",", "\n").split():
        slot, _sep, variant = token.partition(":")
        if slot and variant:
            parts.add((slot, variant))
    return parts


class GamificationReward(models.Model):
    _inherit = "bf.gamification.reward"

    avatar_style = fields.Selection(
        [s for s in render.STYLES if s[0] in render.CHARACTER_STYLES],
        string="Avatar style",
        help="This reward unlocks avatar parts in this style.")
    avatar_parts = fields.Text(
        string="Avatar parts",
        help='One "part:variant" per line, for example "head:mohawk".')
    avatar_part_count = fields.Integer(compute="_compute_avatar_part_count", string="Parts")

    @api.depends("avatar_parts")
    def _compute_avatar_part_count(self):
        for reward in self:
            reward.avatar_part_count = len(parse_parts(reward.avatar_parts))

    @api.constrains("avatar_style", "avatar_parts")
    def _check_avatar_parts(self):
        for reward in self.filtered("avatar_parts"):
            if not reward.avatar_style:
                raise ValidationError(self.env._("Pick the avatar style these parts belong to."))
            slots = render.load_style(reward.avatar_style)["slots"]
            unknown = sorted("%s:%s" % p for p in parse_parts(reward.avatar_parts)
                             if p[0] not in slots or p[1] not in slots[p[0]])
            if unknown:
                raise ValidationError(self.env._("Unknown avatar parts: %s", ", ".join(unknown)))

    def _avatar_part_set(self):
        self.ensure_one()
        return parse_parts(self.avatar_parts) if self.avatar_style else set()


class GamificationRewardClaim(models.Model):
    _inherit = "bf.gamification.reward.claim"

    @api.model_create_multi
    def create(self, vals_list):
        style = self.env["bf.avatar"]._style()
        for vals in vals_list:
            reward = self.env["bf.gamification.reward"].browse(vals.get("reward_id"))
            if not reward.avatar_parts:
                continue
            if reward.avatar_style != style:
                raise UserError(self.env._("These avatar parts belong to a style this database does not use."))
        claims = super().create(vals_list)
        # Avatar parts are self-service: nobody has to approve a hairstyle. The
        # approval is written after creation, as the base create no longer
        # accepts a state from an ordinary user.
        for claim in claims.filtered(lambda c: c.reward_id.avatar_parts):
            claim.sudo().write({"state": "approved", "date_processed": fields.Datetime.now(),
                                "approved_by": claim.user_id.id})
        return claims
