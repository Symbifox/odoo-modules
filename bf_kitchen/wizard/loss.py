from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_is_zero

COOK = "bf_kitchen.group_kitchen_cook"


class KitchenLoss(models.TransientModel):
    """A loss declared in the kitchen: what, how much, why. It is a fact, not a request: the scrap
    is validated even when Odoo's figures say there is not that much in stock (the native window
    "insufficient quantity" is not shown). A dish lost is exploded into its ingredients, as Odoo
    does for a kit."""
    _name = "bf.kitchen.loss"
    _description = "Declare a kitchen loss"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    product_id = fields.Many2one(
        "product.product", "What", required=True, check_company=True,
        domain="[('product_tmpl_id.bf_kitchen_type', 'in', ('ingredient', 'preparation', 'dish'))]")
    uom_category_id = fields.Many2one(related="product_id.uom_id.category_id")
    product_uom_id = fields.Many2one(
        "uom.uom", "Unit", compute="_compute_product_uom_id", store=True, readonly=False, required=True,
        precompute=True,
        domain="[('category_id', '=', uom_category_id)]")
    quantity = fields.Float("How much", required=True, default=1.0, digits="Product Unit of Measure")
    reason_id = fields.Many2one("stock.scrap.reason.tag", "Why", required=True,
                                default=lambda self: self.env["stock.scrap.reason.tag"]._bf_kitchen_reason("expired"))
    location_id = fields.Many2one(
        "stock.location", "Taken from", required=True, check_company=True,
        compute="_compute_location_id", store=True, readonly=False, precompute=True,
        domain="[('usage', '=', 'internal'), ('company_id', 'in', (company_id, False))]")
    note = fields.Char("Note")

    @api.depends("product_id")
    def _compute_product_uom_id(self):
        for loss in self:
            loss.product_uom_id = loss.product_id.uom_id

    @api.depends("product_id", "company_id")
    def _compute_location_id(self):
        for loss in self:
            stored = loss.product_id.with_company(loss.company_id).product_tmpl_id.bf_kitchen_location_id
            if stored:
                loss.location_id = stored
                continue
            warehouse = self.env["stock.warehouse"].search([("company_id", "=", loss.company_id.id)], limit=1)
            loss.location_id = warehouse.lot_stock_id

    def action_declare(self):
        self.ensure_one()
        if not self.env.user.has_group(COOK):
            raise AccessError(_("Declaring a kitchen loss takes the Cook or Kitchen manager rights."))
        if float_is_zero(self.quantity, precision_rounding=self.product_uom_id.rounding) or self.quantity < 0:
            raise UserError(_("Give a quantity above 0."))
        if self.product_uom_id.category_id != self.product_id.uom_id.category_id:
            raise UserError(_("The unit must be one of the item's own kind (weight, volume or units)."))
        product = self.product_id.with_company(self.company_id)
        bom = False
        if product.is_kits:
            bom = self.env["mrp.bom"].sudo()._bom_find(product, company_id=self.company_id.id,
                                                       bom_type="phantom")[product]
        elif not product.is_storable:
            raise UserError(_("%s is not kept in stock: there is nothing to take out.", product.display_name))
        scrap = self.env["stock.scrap"].create({
            "company_id": self.company_id.id,
            "product_id": product.id,
            "product_uom_id": self.product_uom_id.id,
            "scrap_qty": self.quantity,
            "location_id": self.location_id.id,
            "bom_id": bom.id if bom else False,
            "scrap_reason_tag_ids": [fields.Command.set(self.reason_id.ids)],
            "origin": self.note or _("Kitchen loss"),
        })
        scrap.do_scrap()
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "sticky": False,
                       "message": _("Loss recorded: %(qty)s %(unit)s of %(item)s.", qty=self.env["mrp.bom"]._bf_format_number(self.quantity),
                                    unit=self.product_uom_id.name, item=product.display_name),
                       "next": {"type": "ir.actions.act_window_close"}},
        }
