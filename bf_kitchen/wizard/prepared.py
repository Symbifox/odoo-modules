from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_is_zero

COOK = "bf_kitchen.group_kitchen_cook"


class KitchenPrepared(models.TransientModel):
    """"I prepared 4 L of stock": one manufacturing order, confirmed and done in one click. The
    cook has no Manufacturing rights; the order is made as superuser once the cook, the recipe
    and the company are checked, and carries the cook as its responsible."""
    _name = "bf.kitchen.prepared"
    _description = "Kitchen: I prepared"
    _check_company_auto = True

    bom_id = fields.Many2one(
        "mrp.bom", "Preparation", required=True, check_company=True,
        domain="[('bf_kitchen_type', '=', 'preparation'), ('type', '=', 'normal')]")
    company_id = fields.Many2one(related="bom_id.company_id")
    uom_category_id = fields.Many2one(related="bom_id.product_uom_id.category_id")
    product_uom_id = fields.Many2one(
        "uom.uom", "Unit", compute="_compute_product_uom_id", store=True, readonly=False, required=True,
        precompute=True,
        domain="[('category_id', '=', uom_category_id)]")
    quantity = fields.Float("Quantity made", required=True, digits="Product Unit of Measure")

    @api.depends("bom_id")
    def _compute_product_uom_id(self):
        for wizard in self:
            wizard.product_uom_id = wizard.bom_id.product_uom_id
            if wizard.bom_id and not wizard.quantity:
                wizard.quantity = wizard.bom_id.product_qty

    def action_done(self):
        self.ensure_one()
        if not self.env.user.has_group(COOK):
            raise AccessError(_("Recording a preparation takes the Cook or Kitchen manager rights."))
        bom = self.bom_id
        bom.check_access("read")
        if bom.bf_kitchen_type != "preparation" or bom.type != "normal" or not bom.product_tmpl_id.is_storable:
            raise UserError(_("%s is not held in stock: it is made where it is used.",
                              bom.product_tmpl_id.display_name))
        if float_is_zero(self.quantity, precision_rounding=self.product_uom_id.rounding) or self.quantity < 0:
            raise UserError(_("Give a quantity above 0."))
        if self.product_uom_id.category_id != bom.product_uom_id.category_id:
            raise UserError(_("The unit must be one of the preparation's own kind."))
        company = bom.company_id or self.env.company
        if company not in self.env.user.company_ids:
            raise AccessError(_("This preparation belongs to a company you do not work for."))
        product = bom.product_id or bom.product_tmpl_id.product_variant_id
        Production = self.env["mrp.production"].sudo().with_company(company)
        production = Production.create({
            "product_id": product.id, "bom_id": bom.id, "product_qty": self.quantity,
            "product_uom_id": self.product_uom_id.id, "company_id": company.id,
            "user_id": self.env.user.id, "origin": _("Kitchen"),
        })
        production.action_confirm()
        production.qty_producing = self.quantity
        production._set_qty_producing()
        production.move_raw_ids.picked = True
        production.with_context(skip_consumption=True, skip_backorder=True, mo_ids_to_backorder=[],
                                skip_redirection=True).button_mark_done()
        if production.state != "done":
            raise UserError(_("The preparation could not be recorded (%s).", production.state))
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "sticky": False,
                       "message": _("Recorded: %(qty)s %(unit)s of %(prep)s (%(ref)s).", qty=self.env["mrp.bom"]._bf_format_number(self.quantity),
                                    unit=self.product_uom_id.name, prep=product.display_name,
                                    ref=production.name),
                       "next": {"type": "ir.actions.act_window_close"}},
        }
