from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestKitchenBrowser(HttpCase):
    """The Kitchen screens load in the browser for the cook and the manager, without a client error."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        g = env.ref("uom.product_uom_gram")
        unit = env.ref("uom.product_uom_unit")
        P = env["product.product"]
        salmon = P.create({"name": "Saumon navigateur", "type": "consu", "is_storable": True,
                           "bf_kitchen_type": "ingredient", "uom_id": g.id, "uom_po_id": g.id, "bf_yield_pct": 50,
                           "bf_allergen_input_ids": [Command.set(env.ref("bf_food_allergen.allergen_fish").ids)]})
        dish = P.create({"name": "Tartare navigateur", "type": "consu", "bf_kitchen_type": "dish",
                         "list_price": 20, "uom_id": unit.id, "uom_po_id": unit.id})
        cls.bom = env["mrp.bom"].create({
            "product_tmpl_id": dish.product_tmpl_id.id, "type": "phantom", "product_qty": 1,
            "product_uom_id": unit.id,
            "bom_line_ids": [Command.create({"product_id": salmon.id, "bf_net_qty": 120, "product_uom_id": g.id})]})
        new_test_user(env, login="kitchen_cook_web", password="kitchen_cook_web",
                      groups="base.group_user,bf_kitchen.group_kitchen_cook")
        new_test_user(env, login="kitchen_chef_web", password="kitchen_chef_web",
                      groups="base.group_user,bf_kitchen.group_kitchen_manager")

    def _open(self, path, ready, login):
        self.browser_js(path, "console.log('test successful')", ready, login=login, timeout=90)

    def test_cook_screens(self):
        login = "kitchen_cook_web"
        self._open("/odoo/action-bf_kitchen.bf_kitchen_dish_action", "document.querySelector('.o_kanban_record:not(.o_kanban_ghost)')", login)
        self._open(f"/odoo/action-bf_kitchen.bf_kitchen_dish_action/{self.bom.id}", "document.querySelector('.o_field_one2many .o_data_row')", login)
        self._open("/odoo/action-bf_kitchen.bf_kitchen_ingredient_action", "document.querySelector('.o_list_renderer .o_data_row')", login)

    def test_manager_screens(self):
        login = "kitchen_chef_web"
        self._open("/odoo/action-bf_kitchen.bf_kitchen_dish_action", "document.querySelector('.o_kanban_record:not(.o_kanban_ghost)')", login)
        self._open(f"/odoo/action-bf_kitchen.bf_kitchen_dish_action/{self.bom.id}", "document.querySelector('.o_field_one2many .o_data_row')", login)
        self._open("/odoo/action-bf_kitchen.bf_kitchen_preparation_action", "document.querySelector('.o_list_renderer, .o_view_nocontent')", login)
        self._open("/odoo/action-bf_kitchen.bf_kitchen_ingredient_action", "document.querySelector('.o_list_renderer .o_data_row')", login)
