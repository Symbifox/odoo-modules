# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

REASONS = [
    ("price", "Price changed"),
    ("new", "New on the shelf"),
    ("manual", "Asked for"),
]


class RetailLabelQueue(models.Model):
    """What has changed price and does not say so on the shelf yet.

    A price written in Odoo and not on the shelf edge is worse than no change at all: the till
    charges one price and the label promises another. The queue is the walk list, and it is
    marked printed by the person who walked it, not by the button that opened the printer:
    nothing here can observe a printer.
    """
    _name = "bf.retail.label.queue"
    _description = "Shelf label to print"
    _order = "printed, date_added desc, id desc"
    _check_company_auto = True

    product_id = fields.Many2one(
        "product.product", "Product", required=True, index=True, ondelete="cascade",
        check_company=True)
    company_id = fields.Many2one(
        "res.company", "Company", required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_added = fields.Datetime("Queued on", default=fields.Datetime.now, required=True)
    reason = fields.Selection(REASONS, "Why", default="manual", required=True)
    price = fields.Monetary("Price to print", currency_field="currency_id")
    change_line_id = fields.Many2one(
        "bf.retail.price.change.line", "From the batch", ondelete="set null")
    change_id = fields.Many2one(related="change_line_id.change_id", string="Batch", store=True)
    printed = fields.Boolean("Printed", default=False, index=True)
    printed_on = fields.Datetime("Printed on", readonly=True)
    printed_by = fields.Many2one("res.users", "Printed by", readonly=True)

    @api.depends("product_id")
    def _compute_display_name(self):
        for row in self:
            row.display_name = row.product_id.display_name or ""

    def action_open_labels(self):
        """Odoo's own label layout, on the products of the queue rows selected.

        The module does not draw a label: Odoo prints product labels with a price, and a
        second drawing of the same thing would be one more thing to keep.
        """
        rows = self.filtered(lambda row: not row.printed) or self
        products = rows.product_id
        if not products:
            raise UserError(_("Nothing to print."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Print labels"),
            "res_model": "product.label.layout",
            "view_mode": "form",
            "target": "new",
            "context": dict(
                self.env.context,
                default_product_ids=products.ids,
                active_model="product.product",
                active_ids=products.ids,
            ),
        }

    def action_mark_printed(self):
        """Marked by the person who walked the aisle: the queue cannot see a printer.

        🔴 The clerk has no write right on this model any more: with one, and no guard on the
        fields, a clerk rewrote the price the label announces (2.49 to 0.99), its reason, then
        the product of the row. The three fields of the walk are written here, under sudo, on
        the rows the clerk can READ -- the record rule of the model holds the scope, and the
        read above it raises before this line on a row of another store.
        """
        rows = self.filtered(lambda row: not row.printed)
        rows.sudo().write({
            "printed": True,
            "printed_on": fields.Datetime.now(),
            "printed_by": self.env.user.id,
        })
        return True
