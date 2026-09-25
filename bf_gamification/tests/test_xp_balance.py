from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestXpBalance(TransactionCase):
    """Réclamer une récompense puise dans le solde, jamais dans l'XP gagné."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="fq_balance", name="Fox Balance")
        cls.profile = cls.env["bf.gamification.profile"]._get_or_create_profile(cls.user)
        cls.env["bf.gamification.xp.transaction"].sudo().create({
            "user_id": cls.user.id, "xp_amount": 320, "source": "manual",
            "description": "Montage",
        })
        cls.profile._compute_xp()
        cls.profile._compute_level()
        cls.reward = cls.env["bf.gamification.reward"].create({"name": "Chapeau", "xp_cost": 50})

    def test_claim_spends_the_balance_and_keeps_the_level(self):
        level = self.profile.level_id
        self.assertEqual((self.profile.total_xp, self.profile.xp_balance), (320, 320))
        self.env["bf.gamification.reward.claim"].create({
            "user_id": self.user.id, "reward_id": self.reward.id})
        self.profile.invalidate_recordset()
        self.assertEqual(self.profile.total_xp, 320)
        self.assertEqual(self.profile.xp_balance, 270)
        self.profile._compute_level()
        self.assertEqual(self.profile.level_id, level)

    def test_refusal_gives_the_balance_back(self):
        claim = self.env["bf.gamification.reward.claim"].create({
            "user_id": self.user.id, "reward_id": self.reward.id})
        claim.action_refuse()
        self.profile.invalidate_recordset()
        self.assertEqual((self.profile.total_xp, self.profile.xp_balance), (320, 320))

    def test_the_balance_decides_what_can_be_bought(self):
        dear = self.env["bf.gamification.reward"].create({"name": "Cape", "xp_cost": 300})
        self.env["bf.gamification.reward.claim"].create({"user_id": self.user.id, "reward_id": dear.id})
        # 320 earned, 20 left: the 50 XP hat is out of reach even though 320 >= 50.
        with self.assertRaises(UserError):
            self.env["bf.gamification.reward.claim"].create({
                "user_id": self.user.id, "reward_id": self.reward.id})


@tagged("post_install", "-at_install")
class TestClaimAndProfileRights(TransactionCase):
    """Ce qu'un usager ordinaire ne peut plus faire lui-même."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="fq_rights", name="Fox Rights",
                                 groups="base.group_user,bf_gamification.group_gamification_user")
        cls.other = new_test_user(cls.env, login="fq_other", name="Fox Other",
                                  groups="base.group_user,bf_gamification.group_gamification_user")
        cls.profile = cls.env["bf.gamification.profile"]._get_or_create_profile(cls.user)
        cls.env["bf.gamification.xp.transaction"].sudo().create({
            "user_id": cls.user.id, "xp_amount": 100, "source": "manual", "description": "Montage"})
        cls.profile._compute_xp()
        cls.reward = cls.env["bf.gamification.reward"].create({"name": "Journée flex", "xp_cost": 40})

    def test_claim_cannot_approve_itself_or_name_someone_else(self):
        claim = self.env["bf.gamification.reward.claim"].with_user(self.user).create({
            "reward_id": self.reward.id, "state": "approved", "approved_by": self.user.id,
            "xp_spent": 0, "user_id": self.other.id})
        self.assertEqual(claim.state, "pending")
        self.assertFalse(claim.approved_by)
        self.assertEqual(claim.xp_spent, 40)
        self.assertEqual(claim.user_id, self.user)

    def test_user_cannot_write_a_profile(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env["bf.gamification.profile"].with_user(self.user).browse(
                self.profile.id).write({"total_xp": 99999})

    def test_xp_is_still_credited_from_a_user_action(self):
        before = self.profile.total_xp
        Profile = self.env["bf.gamification.profile"].with_user(self.user)
        Profile._get_or_create_profile(self.other)._award_xp(5, "manual", "Essai")
        other = self.env["bf.gamification.profile"]._get_or_create_profile(self.other)
        self.assertEqual(other.total_xp, 5)
        self.assertEqual(self.profile.total_xp, before)
