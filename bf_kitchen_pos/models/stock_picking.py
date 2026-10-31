import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_is_zero

_logger = logging.getLogger(__name__)

# The recipes whose ingredients do not go back to the shelf when the plate comes back.
RETURNED_AS_LOSS = ("dish", "preparation")


class StockScrap(models.Model):
    _inherit = "stock.scrap"

    bf_kitchen_return_move_id = fields.Many2one(
        "stock.move", "Register return", readonly=True, copy=False, index="btree_not_null",
        help="The register return whose ingredients this loss takes out again.")

    # Set by the register return only: a loss linked by hand to a return would keep that return
    # from ever being taken out as a loss.
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            if any(v.get("bf_kitchen_return_move_id") for v in vals_list):
                raise AccessError(_("The link between a loss and a register return is set by the kitchen itself."))
            self = self.with_context({k: v for k, v in self.env.context.items()
                                      if k != "default_bf_kitchen_return_move_id"})
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and "bf_kitchen_return_move_id" in vals:
            raise AccessError(_("The link between a loss and a register return is set by the kitchen itself."))
        return super().write(vals)


class StockMove(models.Model):
    _inherit = "stock.move"

    bf_kitchen_return_scrap_ids = fields.One2many("stock.scrap", "bf_kitchen_return_move_id", readonly=True)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _action_done(self):
        res = super()._action_done()
        self._bf_kitchen_scrap_register_returns()
        return res

    def write(self, vals):
        # The register validates its transfer first and links it to its order or session after
        # (pos.order._create_order_picking): the return is known as the register's only then.
        res = super().write(vals)
        if {"pos_order_id", "pos_session_id"} & set(vals):
            self._bf_kitchen_scrap_register_returns()
        return res

    def _bf_kitchen_register_returns(self):
        return self.filtered(lambda p: p.state == "done" and (p.pos_order_id or p.pos_session_id)
                             and p.location_id.usage == "customer")

    def _bf_kitchen_scrap_register_returns(self):
        """A dish refunded at the register: Odoo puts its ingredients back in stock (a kit is
        returned as its ingredients). The plate came back to the kitchen, not to the shelf: the
        ingredients leave again as a loss, reason Customer return. Done only once the return
        transfer is done (the register validates it when it can, the stock team otherwise),
        once per returned move. An item sold as it is (not from a kitchen recipe) stays back in
        stock. A loss that fails never blocks the return: it is written on the transfer."""
        Scrap = self.env["stock.scrap"].sudo()
        reason = self.env["stock.scrap.reason.tag"]._bf_kitchen_reason("customer_return")
        for picking in self.sudo()._bf_kitchen_register_returns():
            moves = picking.move_ids.filtered(
                lambda m: m.state == "done" and m.bom_line_id
                and m.bom_line_id.bom_id.product_tmpl_id.bf_kitchen_type in RETURNED_AS_LOSS
                and m.location_dest_id.usage == "internal" and not m.bf_kitchen_return_scrap_ids
                and not float_is_zero(m.quantity, precision_rounding=m.product_uom.rounding))
            if not moves:
                continue
            try:
                with self.env.cr.savepoint():
                    for move in moves:
                        scrap = Scrap.create({
                            "product_id": move.product_id.id,
                            "scrap_qty": move.quantity,
                            "product_uom_id": move.product_uom.id,
                            "location_id": move.location_dest_id.id,
                            "company_id": picking.company_id.id,
                            "origin": picking.origin or picking.name,
                            "scrap_reason_tag_ids": [fields.Command.set(reason.ids)],
                            "bf_kitchen_return_move_id": move.id,
                        })
                        scrap.do_scrap()
            except (UserError, ValidationError) as error:
                _logger.warning("Kitchen loss of the register return %s failed: %s", picking.id, error)
                picking._message_log(body=_(
                    "The returned dishes were not taken out as a loss (%s): declare the loss by hand.",
                    error.args[0] if error.args else error))

    def _bf_kitchen_variance_source(self):
        if self.pos_order_id or self.pos_session_id:
            return _("Register")
        return super()._bf_kitchen_variance_source()
