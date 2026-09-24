"""Isolation par personne de « Mon bureau »."""
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMenageBureau(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a = new_test_user(cls.env, login="menage_bureau_a", groups="base.group_user")
        cls.b = new_test_user(cls.env, login="menage_bureau_b", groups="base.group_user")
        cls.action = cls.env.ref("base.action_partner_form", raise_if_not_found=False) or \
            cls.env["ir.actions.act_window"].search([("res_model", "=", "res.partner")], limit=1)

    def _en(self, user, model):
        self.env.invalidate_all()
        return self.env[model].with_user(user)

    def _bureau_a(self):
        desk = self._en(self.a, "bf.bureau.desk").create({"name": "Bureau fictif de A", "layout": "two_columns"})
        # ⚠️ Semé en sudo : une personne non administratrice ne peut pas créer
        # de panneau aujourd'hui (la contrainte _check_view_type_in_action lit
        # ir.actions.act_window, fermé aux internes). Défaut signalé,
        # hors isolation. Le panneau reste sur le bureau de A.
        pane = self.env["bf.bureau.pane"].create({
            "desk_id": desk.id, "slot": "left_full", "action_id": self.action.id, "view_type": "list",
            "domain_override": "[('name', 'ilike', 'secret fictif')]",
        })
        self.env.invalidate_all()
        return desk, pane

    def test_b_ne_voit_ni_bureau_ni_panneau(self):
        desk, pane = self._bureau_a()
        for model, rec in (("bf.bureau.desk", desk), ("bf.bureau.pane", pane)):
            with self.subTest(model=model):
                M = self._en(self.b, model)
                self.assertFalse(M.search([("id", "=", rec.id)]))
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.id).read(["display_name"])
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.id).write({"name": "piraté"} if model == "bf.bureau.desk" else {"weight": 3})
                with self.assertRaises(AccessError):
                    self._en(self.b, model).browse(rec.id).unlink()

    def test_methodes_rpc_ne_rendent_pas_le_bureau_de_a(self):
        desk, _pane = self._bureau_a()
        with self.assertRaises(AccessError):
            self._en(self.b, "bf.bureau.desk").read_desk_for_render(desk.id)
        self.assertNotIn(desk.id, [d["id"] for d in self._en(self.b, "bf.bureau.desk").list_user_desks()])
        self.assertNotEqual(self._en(self.b, "bf.bureau.desk").get_default_desk_id(), desk.id)
        with self.assertRaises(AccessError):
            self._en(self.b, "bf.bureau.desk").browse(desk.id).action_duplicate_for_me()

    def test_b_ne_greffe_pas_de_panneau_sur_le_bureau_de_a(self):
        desk, _pane = self._bureau_a()
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "bf.bureau.pane").create({
                    "desk_id": desk.id, "slot": "right_full", "action_id": self.action.id, "view_type": "list"})
        self.assertEqual(len(desk.sudo().pane_ids), 1, "B a greffé un panneau sur le bureau de A")
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self._en(self.b, "bf.bureau.desk").create({"name": "x", "user_id": self.a.id})

    def test_a_retrouve_son_bureau(self):
        desk, _pane = self._bureau_a()
        data = self._en(self.a, "bf.bureau.desk").read_desk_for_render(desk.id)
        self.assertEqual(data["desk"]["id"], desk.id)
