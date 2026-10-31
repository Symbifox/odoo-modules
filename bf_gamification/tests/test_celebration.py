"""Une fête ne se montre qu'une fois, quel que soit le nombre d'onglets."""
import json
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.bf_gamification.models import gamification_celebration
from odoo.addons.bf_gamification.tests.common import in_a_request


def _bus_payloads(env, kind):
    """Les messages `kind` envoyés par l'essai : Odoo 18 les garde au précommit
    jusqu'à la validation, que l'essai n'atteint pas (la table, elle, porte les
    vieux messages de la base copiée)."""
    pending = env.cr.precommit.data.get("bus.bus.values", [])
    return [json.loads(v["message"])["payload"] for v in pending if kind in v["message"]]


@tagged("post_install", "-at_install")
class TestCelebrationToken(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="fete.une", groups="base.group_user")
        cls.other = new_test_user(cls.env, login="fete.autre", groups="base.group_user")
        cls.Celebration = cls.env["bf.gamification.celebration"]
        cls.Profile = cls.env["bf.gamification.profile"]
        cls.profile = cls.Profile._get_or_create_profile(cls.user)

    def _claim(self, token, user=None):
        return self.Celebration.with_user(user or self.user)._bf_claim(token)

    def _row(self, token):
        return self.Celebration.sudo().search([("token", "=", token)])

    def test_the_first_claim_wins_and_the_next_ones_are_silent(self):
        token = self.Celebration._bf_issue(self.user, "caught_up")
        self.assertTrue(self._claim(token))
        self.assertFalse(self._claim(token))
        self.assertFalse(self._claim(token))
        self.assertTrue(self._row(token).claimed_at)

    def test_someone_else_cannot_claim_nor_spend_my_celebration(self):
        token = self.Celebration._bf_issue(self.user, "caught_up")
        self.assertFalse(self._claim(token, self.other))
        self.assertFalse(self._row(token).claimed_at)
        self.assertTrue(self._claim(token))

    def test_an_unknown_or_malformed_token_is_refused(self):
        for token in (None, "", "inconnu", 42, ["x"], {"token": "x"}):
            self.assertFalse(self._claim(token), token)

    def _age(self, token, age):
        """Vieillit un jeton sur l'horloge de la base (celle de la réclamation), pas celle de Python."""
        self.env.cr.execute("""UPDATE bf_gamification_celebration
                                  SET create_date = (now() AT TIME ZONE 'UTC') - %s WHERE token = %s""",
                            (age, token))

    def test_a_stale_celebration_no_longer_shows(self):
        token = self.Celebration._bf_issue(self.user, "badge")
        self._age(token, gamification_celebration.CLAIM_VALIDITY + timedelta(minutes=1))
        self.assertFalse(self._claim(token))

    def test_a_celebration_just_inside_its_validity_still_shows(self):
        token = self.Celebration._bf_issue(self.user, "badge")
        self._age(token, gamification_celebration.CLAIM_VALIDITY - timedelta(minutes=5))
        self.assertTrue(self._claim(token))

    def test_a_support_session_seeing_odoo_as_me_does_not_take_my_celebration(self):
        token = self.Celebration._bf_issue(self.user, "caught_up")
        in_a_request(self, impersonated=True)
        self.assertFalse(self._claim(token))
        self.assertFalse(self._row(token).claimed_at)

    def test_tokens_are_unique_and_unguessable(self):
        tokens = {self.Celebration._bf_issue(self.user, "caught_up") for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        self.assertTrue(all(len(t) >= 24 for t in tokens))

    def test_an_employee_cannot_read_the_tokens(self):
        self.Celebration._bf_issue(self.user, "caught_up")
        with self.assertRaises(Exception):
            self.Celebration.with_user(self.user).search([])

    def test_old_tokens_are_swept(self):
        old_token = self.Celebration._bf_issue(self.user, "caught_up")
        new_token = self.Celebration._bf_issue(self.user, "caught_up")
        self._age(old_token, gamification_celebration.RETENTION + timedelta(days=1))
        self.Celebration.invalidate_model()
        self.Celebration._gc_celebrations()
        self.assertFalse(self._row(old_token))
        self.assertTrue(self._row(new_token))

    # -- Chaque fête part avec son jeton --------------------------------

    def test_the_caught_up_fox_carries_a_claim_for_its_person(self):
        self.profile._bf_notify_caught_up("inbox")
        payload = _bus_payloads(self.env, "bf_gamification/caught_up")[-1]
        row = self._row(payload["claim"])
        self.assertEqual((row.user_id, row.kind), (self.user, "caught_up"))

    def test_level_up_and_badge_carry_a_claim(self):
        level = self.env["bf.gamification.level"].search([], limit=1)
        self.profile._notify_level_up(level, level)
        self.assertEqual(self._row(_bus_payloads(self.env, "bf_gamification/level_up")[-1]["claim"]).kind,
                         "level_up")
        badge = self.env["bf.gamification.badge"].search([], limit=1)
        self.profile._notify_badge_earned(badge)
        self.assertEqual(self._row(_bus_payloads(self.env, "bf_gamification/badge_earned")[-1]["claim"]).kind,
                         "badge")

    def test_only_a_complete_card_carries_a_claim(self):
        challenge = self.env["bf.gamification.challenge"].sudo().search([], limit=1)
        line = self.env["bf.gamification.challenge.line"].sudo().create({
            "user_id": self.user.id, "challenge_id": challenge.id,
            "day": fields.Date.today(), "slot": 9, "source": challenge.source,
            "target": challenge.target, "xp_reward": 5})
        self.profile._bf_notify_challenge(line, False)
        self.assertFalse(_bus_payloads(self.env, "bf_gamification/challenge_done")[-1]["claim"])
        self.profile._bf_notify_challenge(line, True)
        self.assertEqual(self._row(_bus_payloads(self.env, "bf_gamification/challenge_done")[-1]["claim"]).kind,
                         "challenge_card")

    def test_no_popup_no_token(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_gamification.popup_enabled", "False")
        before = self.Celebration.sudo().search_count([])
        self.profile._bf_notify_caught_up("inbox")
        self.assertEqual(self.Celebration.sudo().search_count([]), before)


@tagged("post_install", "-at_install")
class TestCelebrationRoute(HttpCase):

    def setUp(self):
        super().setUp()
        self.user = new_test_user(self.env, login="fete.route", password="fete.route-essai",
                                  groups="base.group_user")
        self.token = self.env["bf.gamification.celebration"]._bf_issue(self.user, "caught_up")

    def _post(self):
        return self.url_open("/bf_gamification/celebration/claim",
                             data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": {"token": self.token}}),
                             headers={"Content-Type": "application/json"})

    def test_the_route_answers_true_once_to_its_person(self):
        self.authenticate("fete.route", "fete.route-essai")
        self.assertIs(self._post().json()["result"], True)
        self.assertIs(self._post().json()["result"], False)

    def test_the_route_refuses_an_anonymous_visitor(self):
        body = self._post().json()
        self.assertIn("error", body)
        self.assertFalse(self.env["bf.gamification.celebration"].sudo().search(
            [("token", "=", self.token)]).claimed_at)
