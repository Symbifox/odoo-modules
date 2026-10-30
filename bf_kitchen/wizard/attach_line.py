from odoo import _, api, fields, models
from odoo.exceptions import UserError


class KitchenAttachLine(models.TransientModel):
    _name = "bf.kitchen.attach.line"
    _description = "Attach a bill line to a kitchen ingredient"

    line_id = fields.Many2one("account.move.line", required=True, readonly=True, ondelete="cascade")
    line_name = fields.Char(related="line_id.name", string="Bill line")
    partner_id = fields.Many2one(related="line_id.partner_id", string="Vendor")
    line_quantity = fields.Float(related="line_id.quantity", string="Billed quantity")
    ingredient_id = fields.Many2one(
        "product.product", "Ingredient", required=True,
        domain="[('product_tmpl_id.bf_kitchen_type', '=', 'ingredient')]")
    stock_uom_id = fields.Many2one(related="ingredient_id.uom_id", string="Stock unit")
    pack_qty = fields.Float(
        "Stock units per billed unit", digits="Product Unit of Measure", required=True, default=1.0,
        help="12000 for a case of 12 x 1 L when the stock unit is the ml.")
    pack_uom_id = fields.Many2one(
        "uom.uom", "Billed unit",
        help="The unit of the bill line. The format is remembered for this vendor's lines in this "
             "unit only; empty: the lines with no unit.")
    vendor_code = fields.Char("Vendor's code")
    remember = fields.Boolean(
        "Remember for this vendor", default=True,
        help="Creates the vendor line on the ingredient (vendor's code, name, format and price per "
             "purchase unit), so the next bill, typed or read by OCR, carries the product.")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line = self.env["account.move.line"].browse(res.get("line_id"))
        if line:
            line._bf_kitchen_check_attach_rights()
            res.setdefault("pack_uom_id", line.product_uom_id.id)
            if line.bf_ingredient_id:
                res.setdefault("ingredient_id", line.bf_ingredient_id.id)
                res.setdefault("pack_qty", line.bf_pack_qty or 1.0)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        # The related fields (label, vendor, quantity) are read as superuser: the line must be
        # one this person can read, and attach.
        wizards.line_id._bf_kitchen_check_attach_rights()
        return wizards

    def _bf_vendor_price(self, line):
        """Price of the ingredient's purchase unit, in the bill currency, from this line: net of
        discount and taxes, through the format. False when the line has no quantity."""
        if not line.quantity or not self.pack_qty:
            return False
        product = self.ingredient_id
        per_stock_unit = line.price_subtotal / line.quantity / self.pack_qty
        return product.uom_id._compute_price(per_stock_unit, product.uom_po_id or product.uom_id)

    def action_attach(self):
        self.ensure_one()
        line = self.line_id
        line._bf_kitchen_check_attach_rights()
        if line.move_id.move_type != "in_invoice" or line.display_type != "product":
            raise UserError(_("Only the product lines of a vendor bill can be attached."))
        if line.product_id:
            raise UserError(_("This line already has a product: its cost is read from it."))
        if self.pack_qty <= 0:
            raise UserError(_("The format must be above 0."))
        # The domain of the field is the client's only: checked here, before anything is written
        # as superuser.
        ingredient = self.ingredient_id
        if ingredient.product_tmpl_id.bf_kitchen_type != "ingredient":
            raise UserError(_("%s is not a kitchen ingredient: only an ingredient can be attached.",
                              ingredient.display_name))
        if ingredient.company_id and ingredient.company_id != line.company_id:
            raise UserError(_("%(ingredient)s belongs to another company than the bill.",
                              ingredient=ingredient.display_name))
        ingredient.check_access("read")
        # Kitchen fields only: the posted line itself (label, unit, price, taxes) is not touched.
        line.sudo().write({"bf_ingredient_id": self.ingredient_id.id, "bf_pack_qty": self.pack_qty,
                           "bf_kitchen_ignore": False, "bf_cost_issue": False})
        if self.remember and line.partner_id:
            vendor = line.partner_id.commercial_partner_id
            # As the person, not as superuser: the kitchen scope of vendor lines applies, and only
            # the vendor lines of the bill's company are read and written.
            Info = self.env["product.supplierinfo"]
            info = Info.search([("partner_id", "=", vendor.id),
                                ("product_tmpl_id", "=", ingredient.product_tmpl_id.id),
                                ("company_id", "=", line.company_id.id),
                                ("product_id", "in", [ingredient.id, False]),
                                ("bf_pack_uom_id", "=", self.pack_uom_id.id)], limit=1)
            values = {"product_code": self.vendor_code or info.product_code or False,
                      "product_name": (line.name or "")[:200] or False,
                      "bf_pack_qty": self.pack_qty,
                      "bf_pack_uom_id": self.pack_uom_id.id}
            # The native price of a vendor line is per purchase unit of the product, in the
            # vendor line's currency: never the billed unit's price (a case at 60 $ would make a
            # purchase order of 1000 g cost 60 000 $).
            price = self._bf_vendor_price(line)
            if price is not False:
                values.update(price=price, currency_id=line.currency_id.id, discount=0.0)
            if info:
                info.write(values)
            else:
                Info.create(dict(values, partner_id=vendor.id, company_id=line.company_id.id,
                                 product_tmpl_id=self.ingredient_id.product_tmpl_id.id,
                                 product_id=self.ingredient_id.id))
        line.move_id._bf_kitchen_after_change({line.company_id: self.ingredient_id})
        return {"type": "ir.actions.act_window_close"}
