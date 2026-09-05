import json

from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.bf_universal_search.hooks import enable_ctrl_k_star_default
from odoo.addons.bf_universal_search.models.res_users import PARAM_CTRL_K_STAR

FIELD = "bf_universal_search_ctrl_k"


def _make_user(env, login, **extra):
    return env["res.users"].with_context(no_reset_password=True).create({
        "name": f"Ctrl+K {login}",
        "login": login,
        "groups_id": [(6, 0, [env.ref("base.group_user").id])],
        **extra,
    })


@tagged("bf_universal_search", "universal_search", "ctrl_k")
class TestCtrlKDefault(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Param = cls.env["ir.config_parameter"].sudo()
        cls.Param.set_param(PARAM_CTRL_K_STAR, False)
        cls.user = _make_user(cls.env, "ctrlk_user")
        cls.other = _make_user(cls.env, "ctrlk_other")

    def _set_instance(self, raw):
        self.Param.set_param(PARAM_CTRL_K_STAR, raw)

    # --- defaults ---

    def test_absent_parameter_reads_as_off_and_user_follows_instance(self):
        self.assertEqual(self.user[FIELD], "instance")
        self.assertFalse(self.env["res.users"]._bf_universal_search_instance_ctrl_k_star())
        self.assertFalse(self.user._bf_universal_search_ctrl_k_star())

    def test_instance_parameter_parsing(self):
        # res.config.settings writes "True"; a hand-set "1" must count too.
        for raw, expected in (("True", True), ("1", True), ("true", True),
                              ("False", False), ("0", False), ("", False), (False, False)):
            self._set_instance(raw)
            self.assertEqual(
                self.user._bf_universal_search_ctrl_k_star(), expected, msg=repr(raw)
            )

    # --- user choice beats instance default, both ways ---

    def test_user_star_beats_instance_off(self):
        self._set_instance(False)
        self.user[FIELD] = "star"
        self.assertTrue(self.user._bf_universal_search_ctrl_k_star())

    def test_user_default_beats_instance_on(self):
        self._set_instance("True")
        self.user[FIELD] = "default"
        self.assertFalse(self.user._bf_universal_search_ctrl_k_star())
        # …and the other user, who did not choose, gets the instance default.
        self.assertTrue(self.other._bf_universal_search_ctrl_k_star())

    def test_legacy_empty_value_reads_as_instance(self):
        self._set_instance("True")
        self.user.sudo().write({FIELD: False})
        self.assertTrue(self.user._bf_universal_search_ctrl_k_star())

    # --- who may write what ---

    def test_user_writes_own_choice_from_preferences(self):
        me = self.user.with_user(self.user)
        me.write({FIELD: "star"})
        self.assertEqual(me.read([FIELD])[0][FIELD], "star")

    def test_user_cannot_write_someone_elses_choice(self):
        with self.assertRaises(AccessError):
            self.other.with_user(self.user).write({FIELD: "star"})

    def test_settings_checkbox_round_trip(self):
        Settings = self.env["res.config.settings"]
        Settings.create({"bf_universal_search_ctrl_k_star": True}).execute()
        self.assertTrue(self.user._bf_universal_search_ctrl_k_star())
        self.assertTrue(Settings.create({}).bf_universal_search_ctrl_k_star)
        Settings.create({"bf_universal_search_ctrl_k_star": False}).execute()
        self.assertFalse(self.user._bf_universal_search_ctrl_k_star())


@tagged("bf_universal_search", "universal_search", "ctrl_k", "post_install", "-at_install")
class TestCtrlKSessionInfo(HttpCase):
    """The web client learns the choice from session_info: check the wire."""

    def _session_info(self):
        resp = self.url_open(
            "/web/session/get_session_info",
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": {}}),
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()["result"]

    def test_session_info_carries_effective_choice(self):
        password = "CtrlK-test-password"
        user = _make_user(self.env, "ctrlk_http", password=password)
        self.env["ir.config_parameter"].sudo().set_param(PARAM_CTRL_K_STAR, False)
        self.authenticate("ctrlk_http", password)

        info = self._session_info()
        self.assertIn("bf_universal_search_ctrl_k_star", info)
        self.assertFalse(info["bf_universal_search_ctrl_k_star"])

        user.sudo().write({FIELD: "star"})
        self.assertTrue(self._session_info()["bf_universal_search_ctrl_k_star"])

        user.sudo().write({FIELD: "instance"})
        self.env["ir.config_parameter"].sudo().set_param(PARAM_CTRL_K_STAR, "True")
        self.assertTrue(self._session_info()["bf_universal_search_ctrl_k_star"])


@tagged("bf_universal_search", "universal_search", "ctrl_k")
class TestCtrlKSeededDefault(TransactionCase):
    """18.0.2.2.0 ships the feature ON. The value is SEEDED, never inferred from
    the parameter's absence — see enable_ctrl_k_star_default for why that
    distinction is the whole design."""

    def setUp(self):
        super().setUp()
        self.Param = self.env["ir.config_parameter"].sudo()

    def _drop(self):
        self.Param.search([("key", "=", PARAM_CTRL_K_STAR)]).unlink()

    def test_seeds_true_on_a_database_that_never_had_the_key(self):
        self._drop()
        self.assertTrue(enable_ctrl_k_star_default(self.env))
        self.assertEqual(self.Param.get_param(PARAM_CTRL_K_STAR), "True")

    def test_a_seeded_default_opens_the_star_for_a_user_who_never_chose(self):
        self._drop()
        enable_ctrl_k_star_default(self.env)
        user = _make_user(self.env, "ctrlk_seeded")
        self.assertEqual(user[FIELD], "instance")
        self.assertTrue(user._bf_universal_search_ctrl_k_star())

    def test_never_overrules_an_administrator_who_switched_it_on(self):
        self.Param.set_param(PARAM_CTRL_K_STAR, "True")
        self.assertFalse(enable_ctrl_k_star_default(self.env))

    def test_never_overrules_an_administrator_who_switched_it_off(self):
        # A stored falsy value is what an explicit "off" looks like when it was
        # written by anything other than the settings checkbox.
        self.Param.set_param(PARAM_CTRL_K_STAR, "False")
        self.assertFalse(enable_ctrl_k_star_default(self.env))
        self.assertEqual(self.Param.get_param(PARAM_CTRL_K_STAR), "False")

    def test_is_idempotent(self):
        self._drop()
        self.assertTrue(enable_ctrl_k_star_default(self.env))
        self.assertFalse(enable_ctrl_k_star_default(self.env))
        self.assertEqual(
            self.Param.search_count([("key", "=", PARAM_CTRL_K_STAR)]), 1)

    def test_a_user_who_chose_the_commands_still_wins_over_the_seed(self):
        self._drop()
        enable_ctrl_k_star_default(self.env)
        user = _make_user(self.env, "ctrlk_seeded_optout")
        user.write({FIELD: "default"})
        self.assertFalse(user._bf_universal_search_ctrl_k_star())
