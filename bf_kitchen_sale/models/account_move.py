import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.bf_kitchen.models.kitchen_consumption import SALE_TYPES

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    bf_kitchen_consumption_ids = fields.One2many(
        "stock.picking", "bf_kitchen_invoice_id", "Kitchen consumptions", readonly=True)
    bf_kitchen_consumption_count = fields.Integer(compute="_compute_bf_kitchen_consumption_count")

    @api.depends("bf_kitchen_consumption_ids")
    def _compute_bf_kitchen_consumption_count(self):
        for move in self:
            move.bf_kitchen_consumption_count = len(move.sudo().bf_kitchen_consumption_ids)

    def _bf_kitchen_invoice_lines(self):
        """The kitchen lines this invoice alone takes out of the stock.

        A line of a sales order is left to its delivery (an order invoiced on ordered quantities
        is invoiced before it is delivered), an invoice of the register to the register's own
        transfer: either would be counted twice.
        """
        self.ensure_one()
        if self.move_type != "out_invoice":
            return self.env["account.move.line"]
        if "pos_order_ids" in self._fields and self.sudo().pos_order_ids:
            return self.env["account.move.line"]
        return self.invoice_line_ids.filtered(
            lambda l: l.display_type == "product" and l.product_id
            and l.product_id.product_tmpl_id.bf_kitchen_type in SALE_TYPES
            and not l.sale_line_ids and l.quantity > 0)

    def _post(self, soft=True):
        posted = super()._post(soft)
        posted.filtered(lambda m: m.move_type == "out_invoice")._bf_kitchen_consume_invoice()
        return posted

    def button_draft(self):
        res = super().button_draft()
        self._bf_kitchen_return_invoice()
        return res

    def _reverse_moves(self, default_values_list=None, cancel=False):
        """Reversed to be cancelled (the credit note's "modify" mode, which then makes a new
        draft): the food goes back, as when the invoice is cancelled; the corrected invoice takes it
        out again when it is posted. A plain credit note is a commercial gesture: the food was
        served, nothing comes back."""
        reverses = super()._reverse_moves(default_values_list, cancel=cancel)
        if cancel:
            self._bf_kitchen_return_invoice()
        return reverses

    def button_cancel(self):
        res = super().button_cancel()
        self._bf_kitchen_return_invoice()
        return res

    def _bf_kitchen_consume_invoice(self):
        """Take the invoice's dishes out of the stock. Never blocks the posting: a consumption
        that fails is written on the invoice, for the kitchen manager to record by hand."""
        Picking = self.env["stock.picking"].sudo()
        for move in self:
            lines = move._bf_kitchen_invoice_lines()
            if not lines or move.sudo().bf_kitchen_consumption_ids._bf_kitchen_still_out():
                continue
            company = move.company_id
            warehouse = company._bf_kitchen_warehouse()
            if not warehouse:
                move._message_log(body=_("The dishes of this invoice did not leave the kitchen stock: "
                                         "the company %s has no warehouse.", company.name))
                continue
            try:
                with self.env.cr.savepoint():
                    picking = Picking._bf_kitchen_consume(
                        warehouse, [(l.product_id, l.quantity, l.product_uom_id) for l in lines],
                        "invoice", move.name, event_date=move.invoice_date, partner=move.partner_id)
                    picking.bf_kitchen_invoice_id = move.id
            except (UserError, ValidationError) as error:
                _logger.warning("Kitchen consumption of invoice %s failed: %s", move.id, error)
                move._message_log(body=_("The dishes of this invoice did not leave the kitchen stock: %s",
                                         error.args[0] if error.args else error))
                continue
            if picking:
                move._message_log(body=_("Dishes taken out of the kitchen stock: %s.", picking._get_html_link()))

    def _bf_kitchen_return_invoice(self):
        for move in self:
            out = move.sudo().bf_kitchen_consumption_ids._bf_kitchen_still_out()
            if not out:
                continue
            returns = out._bf_kitchen_return()
            for ret in returns:
                ret.bf_kitchen_invoice_id = move.id
            if returns:
                move._message_log(body=_("Dishes put back in the kitchen stock: %s.",
                                         Markup(", ").join(r._get_html_link() for r in returns)))

    def action_bf_kitchen_view_consumptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Kitchen consumptions"), "res_model": "stock.picking",
            "view_mode": "list,form", "domain": [("bf_kitchen_invoice_id", "=", self.id)],
            "context": {"create": False},
        }
