from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_avatar.lib import render

from ..models.gamification_reward import parse_parts


@tagged("post_install", "-at_install")
class TestAvatarPacks(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env["ir.config_parameter"].sudo()
        cls.params.set_param("bf_avatar.style", "open_peeps")
        cls.user = new_test_user(cls.env, login="av_fq", name="Fox Avatar",
                                 groups="base.group_user,bf_gamification.group_gamification_user")
        cls.Users = cls.env["res.users"].with_user(cls.user)
        cls.hats = cls.env.ref("bf_avatar_gamification.pack_open_peeps_hats")

    def _give_xp(self, amount):
        self.env["bf.gamification.xp.transaction"].sudo().create({
            "user_id": self.user.id, "xp_amount": amount, "source": "manual", "description": "Montage"})
        self.env["bf.gamification.profile"]._get_or_create_profile(self.user)._compute_xp()

    def test_packs_only_sell_what_a_drawn_avatar_never_uses(self):
        packs = self.env["bf.gamification.reward"].search([("avatar_parts", "!=", False)])
        self.assertEqual(len(packs), 6)
        for pack in packs:
            excluded = render.SEED_EXCLUDE[pack.avatar_style]
            for slot, variant in parse_parts(pack.avatar_parts):
                self.assertIn(slot, excluded, pack.name)
                self.assertTrue(excluded[slot] is None or variant in excluded[slot], (pack.name, variant))
        self.assertEqual(sorted(set(packs.mapped("xp_cost"))), [25, 50, 100])

    def test_locked_part_can_be_tried_on_but_not_saved(self):
        data = self.Users.bf_avatar_composer()
        self.assertEqual(data["xp_balance"], 0)
        head = next(s for s in data["slots"] if s["name"] == "head")
        mohawk = next(v for v in head["variants"] if v["key"] == "mohawk")
        self.assertEqual(mohawk["locked"]["reward_id"], self.hats.id)
        self.assertFalse(mohawk["locked"]["affordable"])
        self.assertFalse(next(v for v in head["variants"] if v["key"] == "afro")["locked"])
        config = dict(data["config"], head="mohawk")
        self.assertIn("preview", self.Users.bf_avatar_preview(config))
        with self.assertRaises(UserError):
            self.Users.bf_avatar_save(config)

    def test_unlocking_spends_the_balance_not_the_level(self):
        self._give_xp(120)
        profile = self.env["bf.gamification.profile"]._get_or_create_profile(self.user)
        profile._compute_level()
        level = profile.level_id
        data = self.Users.bf_avatar_unlock(self.hats.id)
        self.assertEqual(data["xp_balance"], 70)
        claim = self.env["bf.gamification.reward.claim"].search([("user_id", "=", self.user.id)])
        self.assertEqual(claim.state, "approved")
        profile.invalidate_recordset()
        self.assertEqual(profile.total_xp, 120)
        profile._compute_level()
        self.assertEqual(profile.level_id, level)
        self.Users.bf_avatar_save(dict(data["config"], head="mohawk"))
        self.assertEqual(self.user.sudo().bf_avatar_config["head"], "mohawk")
        # One of each per person.
        with self.assertRaises(UserError):
            self.Users.bf_avatar_unlock(self.hats.id)

    def test_refusals(self):
        with self.assertRaises(UserError):  # no XP
            self.Users.bf_avatar_unlock(self.hats.id)
        self._give_xp(500)
        badges = self.env.ref("bf_avatar_gamification.pack_notionists_badges")
        with self.assertRaises(UserError):  # another style's pack
            self.Users.bf_avatar_unlock(badges.id)
        with self.assertRaises(UserError):  # the same pack claimed straight from the shop
            self.env["bf.gamification.reward.claim"].with_user(self.user).create({
                "user_id": self.user.id, "reward_id": badges.id})
        plain = self.env["bf.gamification.reward"].create({"name": "Congé", "xp_cost": 10})
        with self.assertRaises(UserError):  # not an avatar pack
            self.Users.bf_avatar_unlock(plain.id)
        with self.assertRaises(UserError):
            self.Users.bf_avatar_unlock(999999)
