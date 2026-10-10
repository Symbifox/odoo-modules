"""Par le réseau, comme un vrai client (JSON-RPC), dans le rôle d'une personne ordinaire."""
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tests.common import JsonRpcException


@tagged("post_install", "-at_install", "bf_credit_identity")
class TestParLeReseau(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.anne = new_test_user(cls.env, login="credit_anne", name="Anne", groups="base.group_user")
        cls.bruno = new_test_user(cls.env, login="credit_bruno", name="Bruno", groups="base.group_user")
        for user in (cls.anne, cls.bruno):
            cls.env["bf.credit.setup"].with_user(user).create(
                {"start_date": "2031-03-01"}).action_create()
        cls.rappels_bruno = cls.env["bf.credit.reminder"].sudo().search(
            [("user_id", "=", cls.bruno.id)])
        assert len(cls.rappels_bruno) == 4

    def rpc(self, model, method, *args, **kwargs):
        return self.make_jsonrpc_request(f"/web/dataset/call_kw/{model}/{method}", {
            "model": model, "method": method, "args": list(args), "kwargs": kwargs})

    def test_anne_par_le_reseau(self):
        self.authenticate("credit_anne", "credit_anne")
        lus = self.rpc("bf.credit.reminder", "search_read", [], fields=["name", "user_id"])
        self.assertEqual(len(lus), 4)
        self.assertEqual({r["user_id"][0] for r in lus}, {self.anne.id})
        for methode, args in (("read", ([self.rappels_bruno[0].id], ["name"])),
                              ("write", ([self.rappels_bruno[0].id], {"name": "Lu"})),
                              ("unlink", ([self.rappels_bruno[0].id],))):
            with self.subTest(methode=methode), self.assertRaises(JsonRpcException) as e:
                self.rpc("bf.credit.reminder", methode, *args)
            self.assertIn("AccessError", str(e.exception))
        guide = self.rpc("bf.credit.guide", "get_guide_html")
        self.assertIn("Security freeze", guide)

    def test_les_vues_et_les_actions_se_chargent(self):
        self.authenticate("credit_anne", "credit_anne")
        for xmlid in ("bf_credit_identity.action_credit_reminder",
                      "bf_credit_identity.action_credit_setup"):
            with self.subTest(action=xmlid):
                action = self.env.ref(xmlid)
                vues = self.rpc(action.res_model, "get_views",
                                views=[[False, mode] for mode in action.view_mode.split(",")]
                                + [[False, "search"]])
                self.assertTrue(vues["views"])
        client = self.env.ref("bf_credit_identity.action_credit_guide")
        self.assertEqual(client.tag, "bf_credit_identity.guide")
