from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_is_zero

# The kitchen items whose stock the kitchen follows. A dish is a kit: its ingredients move, not it.
STOCK_TYPES = ("ingredient", "preparation")
SALE_TYPES = ("ingredient", "preparation", "dish")
# Written by the module's own code only, in superuser: a source forged by RPC would put a
# delivery in the wrong column of the variance report, or hide it.
PICKING_SERVER_FIELDS = {"bf_kitchen_source", "bf_kitchen_event_date"}


def _bf_without_defaults(env, reserved):
    """The context without the `default_*` keys of these server fields."""
    return {k: v for k, v in env.context.items() if not (k.startswith("default_") and k[8:] in reserved)}


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    bf_kitchen_consumption_type_id = fields.Many2one(
        "stock.picking.type", "Kitchen consumption", copy=False, readonly=True,
        help="The operation type of what the kitchen sends out without a delivery: invoices with no "
             "sales order, school meals, sales imported from another register.")

    def _bf_kitchen_consumption_type(self):
        """The warehouse's Kitchen consumption operation type, made on first use.

        It has to exist: Odoo explodes a kit into its ingredients only on a move that carries an
        operation type (mrp, stock_move.action_explode), so a consumption without one would take
        the dish itself out of a stock it never had, and the ingredients would never leave.
        """
        self.ensure_one()
        warehouse = self.sudo()
        if warehouse.bf_kitchen_consumption_type_id:
            return warehouse.bf_kitchen_consumption_type_id
        # Two first sales at the same moment would make two types.
        self.env.cr.execute("SELECT id FROM stock_warehouse WHERE id = %s FOR UPDATE", [warehouse.id])
        warehouse.invalidate_recordset(["bf_kitchen_consumption_type_id"])
        if warehouse.bf_kitchen_consumption_type_id:
            return warehouse.bf_kitchen_consumption_type_id
        customers = self.env.ref("stock.stock_location_customers")
        ptype = self.env["stock.picking.type"].sudo().with_context(lang="en_US").create({
            "name": "Kitchen consumption",
            "code": "outgoing",
            "sequence_code": "CONS",
            "warehouse_id": warehouse.id,
            "company_id": warehouse.company_id.id,
            "default_location_src_id": warehouse.lot_stock_id.id,
            "default_location_dest_id": customers.id,
            "sequence": 90,
        })
        fr = self.env["res.lang"]._lang_get("fr_CA")
        if fr and fr.active:
            ptype.update_field_translations("name", {"fr_CA": "Consommation cuisine"})
        warehouse.bf_kitchen_consumption_type_id = ptype
        return ptype


class StockPicking(models.Model):
    _inherit = "stock.picking"

    bf_kitchen_source = fields.Selection(
        [("invoice", "Invoice without a sales order"), ("other", "Other")],
        "Kitchen source", readonly=True, copy=False, index="btree_not_null",
        help="Where a kitchen consumption comes from.")
    bf_kitchen_event_date = fields.Date(
        "Sold or served on", readonly=True, copy=False,
        help="The day of the sale or the meal this consumption stands for. A consumption recorded "
             "after a count for a day before it is shown by the variance report.")

    @api.model
    def _bf_kitchen_server_fields(self):
        """Fields of a transfer only the kitchen's own code writes (bridges add theirs)."""
        return set(PICKING_SERVER_FIELDS)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            reserved = self._bf_kitchen_server_fields()
            if any(reserved & {k for k, v in vals.items() if v} for vals in vals_list):
                raise AccessError(_("The kitchen source of a transfer is set by the kitchen itself."))
            # A `default_*` of the context (RPC sends any) would set them after this check.
            self = self.with_context(_bf_without_defaults(self.env, reserved))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and self._bf_kitchen_server_fields() & set(vals):
            raise AccessError(_("The kitchen source of a transfer is set by the kitchen itself."))
        return super().write(vals)

    @api.model
    def _bf_kitchen_consume(self, warehouse, lines, source, origin, event_date=None, partner=None):
        """Take what the kitchen sent out from the stock: one done transfer of the warehouse's
        Kitchen consumption type. A dish (kit) is exploded by Odoo into its ingredients.

        lines: [(product, quantity, unit)]. What is not in stock goes negative, as at the register:
        the food is gone, and the next count shows the difference. Returns the transfer (empty
        when there is nothing to take).
        """
        ptype = warehouse._bf_kitchen_consumption_type()
        company = warehouse.company_id
        moves = []
        for product, qty, uom in lines:
            if product.product_tmpl_id.bf_kitchen_type not in SALE_TYPES or product.type != "consu":
                continue
            if float_compare(qty, 0.0, precision_rounding=uom.rounding) <= 0:
                continue
            moves.append(Command.create({
                "name": product.display_name,
                "product_id": product.id,
                "product_uom_qty": qty,
                "product_uom": uom.id,
                "location_id": ptype.default_location_src_id.id,
                "location_dest_id": ptype.default_location_dest_id.id,
                "company_id": company.id,
                "picking_type_id": ptype.id,
            }))
        if not moves:
            return self.browse()
        picking = self.sudo().with_company(company).create({
            "picking_type_id": ptype.id,
            "location_id": ptype.default_location_src_id.id,
            "location_dest_id": ptype.default_location_dest_id.id,
            "partner_id": partner.id if partner else False,
            "origin": origin,
            "company_id": company.id,
            "bf_kitchen_source": source,
            "bf_kitchen_event_date": event_date,
            "move_ids": moves,
        })
        picking.action_confirm()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids,
                             skip_sms=True, skip_immediate=True).button_validate()
        if picking.state != "done":
            raise UserError(_("The kitchen consumption %s could not be validated.", picking.name))
        return picking

    def _bf_kitchen_return(self):
        """Put back in stock what these done consumptions took and is not returned yet (an invoice
        reset to draft or cancelled). Returns the return transfers."""
        returns = self.browse()
        for picking in self.sudo().filtered(lambda p: p.state == "done"):
            wizard = self.env["stock.return.picking"].sudo().with_context(
                active_id=picking.id, active_ids=picking.ids, active_model="stock.picking").create(
                {"picking_id": picking.id})
            todo = False
            for line in wizard.product_return_moves:
                move = line.move_id
                left = move._bf_kitchen_net_qty()
                left = move.product_id.uom_id._compute_quantity(left, line.uom_id or move.product_id.uom_id)
                line.quantity = max(left, 0.0)
                todo = todo or not float_is_zero(line.quantity, precision_rounding=move.product_id.uom_id.rounding)
            if not todo:
                continue
            wizard.product_return_moves.filtered(lambda l: float_is_zero(
                l.quantity, precision_rounding=l.move_id.product_id.uom_id.rounding)).unlink()
            ret = wizard._create_return()
            ret.action_confirm()
            for move in ret.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            ret.with_context(skip_backorder=True, picking_ids_not_to_backorder=ret.ids, skip_sms=True,
                             skip_immediate=True).button_validate()
            # The return keeps the source of what it returns: the variance report nets it there.
            ret.write({"bf_kitchen_source": picking.bf_kitchen_source,
                       "bf_kitchen_event_date": picking.bf_kitchen_event_date})
            returns |= ret
        return returns

    def _bf_kitchen_variance_source(self):
        """The label of where the food that left with this transfer went, in the variance report."""
        self.ensure_one()
        if self.bf_kitchen_source:
            return dict(self._fields["bf_kitchen_source"]._description_selection(self.env))[self.bf_kitchen_source]
        if "sale_id" in self._fields and self.sale_id:
            return _("Sales orders")
        return _("Deliveries")


class StockMove(models.Model):
    _inherit = "stock.move"

    def _bf_kitchen_variance_kind(self, inside):
        """How a done move of a kitchen item counts in the variance report.

        inside: the ids of the warehouse's internal locations. Returns (kind, sign, reason, source)
        or None when the move stays inside the warehouse. The sign makes the quantity positive in
        the natural direction of its kind (bought in, consumed out, lost out); unexplained is
        counted minus expected, negative when something is missing.
        """
        self.ensure_one()
        src_in = self.location_id.id in inside
        dest_in = self.location_dest_id.id in inside
        if src_in == dest_in:
            return None
        into = 1 if dest_in else -1
        other = self.location_id if dest_in else self.location_dest_id
        if self.is_inventory:
            return "unexplained", into, False, False
        if self.scrapped or self.scrap_id:
            return "loss", -into, self.scrap_id.scrap_reason_tag_ids[:1].id, False
        if other.usage == "supplier":
            return "purchase", into, False, False
        if other.usage == "customer":
            return "consumption", -into, False, self.picking_id._bf_kitchen_variance_source() \
                if self.picking_id else _("Deliveries")
        if other.usage == "production":
            return ("made", 1, False, False) if dest_in else ("used", 1, False, False)
        return "other", into, False, False
