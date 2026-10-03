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
        # Créé par A elle-même. Avant, une personne non administratrice ne
        # pouvait créer aucun panneau (la contrainte
        # _check_view_type_in_action lisait ir.actions.act_window sous ses
        # droits) et l'essai le semait en sudo.
        pane = self._en(self.a, "bf.bureau.pane").create({
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

    def test_non_admin_cree_un_panneau_sur_son_bureau(self):
        """La contrainte de mode de vue ne bloque plus les internes."""
        desk, pane = self._bureau_a()
        self.assertFalse(self.a.has_group("base.group_system"))
        self.assertEqual(pane.sudo().desk_id, desk)
        self.assertEqual(pane.sudo().create_uid, self.a)

    def test_mode_de_vue_absent_toujours_refuse(self):
        """La contrainte tient encore : un mode que l'action n'offre pas est refusé."""
        from odoo.exceptions import ValidationError
        desk, _pane = self._bureau_a()
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._en(self.a, "bf.bureau.pane").create({
                    "desk_id": desk.id, "slot": "right_full", "action_id": self.action.id,
                    "view_type": "calendar"})

    def test_le_selecteur_d_action_s_ouvre_aux_internes(self):
        """Le sélecteur du formulaire du bureau cherche dans
        ir.actions.act_window, fermé aux internes. Avec le drapeau du
        sélecteur, il rend les actions qu'on peut ouvrir, et rien d'autre."""
        Actions = self._en(self.a, "ir.actions.act_window")
        with self.assertRaises(AccessError):
            Actions.name_search(self.action.name)
        trouves = dict(Actions.with_context(bf_bureau_pane_picker=True).name_search(
            self.action.name))
        self.assertIn(self.action.id, trouves)
        reservee = self.env["ir.actions.act_window"].create({
            "name": "Action réservée (essai)", "res_model": "res.users",
            "groups_id": [(6, 0, self.env.ref("base.group_system").ids)]})
        self.assertFalse(Actions.with_context(bf_bureau_pane_picker=True).name_search(
            "Action réservée (essai)"))
        # « Recherche avancée » : la liste suit le même filtre.
        liste = Actions.with_context(bf_bureau_pane_picker=True).web_search_read(
            [("name", "ilike", "réservée (essai)")], {"name": {}})
        self.assertNotIn(reservee.id, [r["id"] for r in liste["records"]])
        liste = Actions.with_context(bf_bureau_pane_picker=True).web_search_read(
            [("id", "=", self.action.id)], {"name": {}})
        self.assertEqual([r["id"] for r in liste["records"]], [self.action.id])
        with self.assertRaises(AccessError):
            Actions.web_search_read([("id", "=", self.action.id)], {"name": {}})
        vues = Actions.with_context(bf_bureau_pane_picker=True).get_views([(False, "list")])
        self.assertIn("list", vues["views"])

    def test_formulaire_du_bureau_se_lit_avec_ses_panneaux(self):
        desk, _pane = self._bureau_a()
        lu = self._en(self.a, "bf.bureau.desk").browse(desk.id).web_read({
            "pane_ids": {"fields": {"action_id": {"fields": {"display_name": {}}},
                                    "view_type": {}}}})
        self.assertEqual(lu[0]["pane_ids"][0]["action_id"]["id"], self.action.id)
