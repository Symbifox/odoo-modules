from odoo import api, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model
    def _bf_pos_allergens(self, product):
        """What the register shows of a kitchen product: its allergens (recipe and sub-recipes
        included) and what it may contain, in the reader's language, in the list's order."""
        tmpl = product.product_tmpl_id.sudo()
        contains = tmpl.bf_allergen_ids.sorted(lambda a: (a.sequence, a.id))
        may = (tmpl.bf_may_contain_ids - tmpl.bf_allergen_ids).sorted(lambda a: (a.sequence, a.id))
        return {
            "bf_kitchen": bool(tmpl.bf_kitchen_type),
            "bf_allergens": contains.mapped("name"),
            "bf_may_contain": may.mapped("name"),
        }

    def get_product_info_pos(self, price, quantity, pos_config_id):
        res = super().get_product_info_pos(price, quantity, pos_config_id)
        # The caller reads this product already (the native call does); the allergens of the
        # recipe go through ingredients and recipes a cashier may not read.
        self.check_access("read")
        res.update(self._bf_pos_allergens(self))
        return res
