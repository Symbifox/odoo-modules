import json
import logging
from collections import defaultdict
from datetime import timedelta
from uuid import uuid4

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero

_logger = logging.getLogger(__name__)
PRECISION = 3


def _sent(raw):
    """(lines, general note) of what the register last sent to the kitchen (last_order_preparation_change):
    lines is {key: line}, the key being the order line's uuid in Odoo 18."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}, ""
    if not isinstance(data, dict):
        return {}, ""
    lines = data.get("lines")
    return (lines if isinstance(lines, dict) else {}), (data.get("generalNote") or "").strip()


def _note(line):
    return (line.get("note") or "").strip()


class PosOrder(models.Model):
    _inherit = "pos.order"

    @api.model
    def _process_order(self, order, existing_order):
        before = existing_order.last_order_preparation_change if existing_order else False
        table_before = existing_order.table_id if existing_order else False
        # A bill split: the register moves dishes already sent from one order to a new one, and sends both
        # in one call, marked (pos_store_patch.js). Nothing new for the kitchen, nothing cancelled.
        split = order.get("uuid") in (self.env.context.get("bf_kitchen_split") or [])
        order_id = super()._process_order(order, existing_order)
        record = self.browse(order_id)
        if record.config_id.bf_kitchen_display:
            if not split:
                record._bf_kitchen_dispatch(before)
            if existing_order and record.table_id != table_before:
                record._bf_kitchen_follow_table()
        return order_id

    # --- what was sent since the last press of Order
    def _bf_kitchen_changes(self, before):
        """[(line, quantity, removed)] since `before`: positive for what was added, negative for what was
        taken off. A line whose note changed is taken off with its old note and sent again with the new
        one (Odoo 18 keeps the key and rewrites the note in place); `removed` is True only for dishes
        taken off for good (a quantity lowered or a line deleted), the ones a cook may have to throw."""
        self.ensure_one()
        old, _old_note = _sent(before)
        new, _new_note = _sent(self.last_order_preparation_change)
        changes = []
        for key in list(new) + [k for k in old if k not in new]:
            n, o = new.get(key), old.get(key)
            nq = (n or {}).get("quantity") or 0.0
            oq = (o or {}).get("quantity") or 0.0
            if n and o and _note(n) != _note(o):
                if not float_is_zero(oq, precision_digits=PRECISION):
                    changes.append((o, -oq, False))
                if not float_is_zero(nq, precision_digits=PRECISION):
                    changes.append((n, nq, False))
                continue
            delta = nq - oq
            if not float_is_zero(delta, precision_digits=PRECISION):
                changes.append((n or o, delta, delta < 0))
        return [c for c in changes if c[0].get("product_id")]

    def _bf_kitchen_dispatch(self, before):
        self.ensure_one()
        Ticket = self.env["bf.kitchen.ticket"].sudo()
        self._bf_kitchen_note_changed(before)
        changes = self._bf_kitchen_absorb_moved(self._bf_kitchen_changes(before))
        if not changes:
            return Ticket
        products = self.env["product.product"].sudo().browse({c[0]["product_id"] for c in changes}).exists()
        sending = uuid4().hex  # the tickets of one press: decided together when cancelled
        stations = self.config_id.sudo().bf_kitchen_station_ids
        by_station = {}
        for line, qty, removed in changes:
            product = products.filtered(lambda p: p.id == line["product_id"])
            if not product or product.type in ("combo", "service"):  # a combo's dishes come as their own lines
                continue
            for station in stations.filtered(lambda s: s._bf_takes(product)):
                by_station.setdefault((station, removed), []).append((product, line, qty))
        allergens = self.env["product.product"]._bf_pos_allergens
        tickets = Ticket
        for (station, removed), lines in by_station.items():
            # Dishes taken off for good: nothing to cook, a cancellation for the cook to decide on.
            extra = {"state": "cancelled", "cancel_reason": "taken_off", "cancelled_at": fields.Datetime.now()} \
                if removed else {}
            tickets |= Ticket.create(dict(self._bf_kitchen_ticket_vals(), **extra, station_id=station.id, sending=sending, line_ids=[
                (0, 0, {"product_id": product.id, "name": line.get("name") or product.display_name,
                        "qty": qty, "note": _note(line) or False, "line_uuid": line.get("uuid"),
                        "allergens": ", ".join(allergens(product)["bf_allergens"]) if qty > 0 else False})
                for product, line, qty in lines]))
        return tickets

    def _bf_kitchen_note_changed(self, before):
        """The order's general note changed after it was sent: the open tickets show the new one, marked."""
        self.ensure_one()
        old, new = _sent(before)[1], _sent(self.last_order_preparation_change)[1]
        if before and old != new:
            self.env["bf.kitchen.ticket"].sudo().search([
                ("order_id", "=", self.id), ("state", "in", ("new", "progress", "ready"))]).write(
                {"general_note": new or False, "note_updated": True})

    def _bf_kitchen_ticket_vals(self):
        self.ensure_one()
        return {
            "company_id": self.company_id.id, "config_id": self.config_id.id, "session_id": self.session_id.id,
            "order_id": self.id, "order_ref": self.pos_reference or self.name,
            "table_id": self.table_id.id, "table_name": self._bf_kitchen_table_name(),
            "customer_count": self.customer_count,
            "sent_by": (self.employee_id.name if "employee_id" in self._fields and self.employee_id else
                        self.user_id.name),
            "general_note": _sent(self.last_order_preparation_change)[1] or False,
        }

    def _bf_kitchen_table_name(self):
        table = self.table_id
        if not table:
            return False
        return "%s %s" % (table.floor_id.name, table.table_number) if table.floor_id else str(table.table_number)

    # --- a table moved
    def _bf_kitchen_follow_table(self):
        """Moved to a free table: the order is the same, its tickets follow it."""
        for order in self:
            self.env["bf.kitchen.ticket"].sudo().search([("order_id", "=", order.id)]).write({
                "table_id": order.table_id.id, "table_name": order._bf_kitchen_table_name()})

    def bf_kitchen_transfer(self, destination_id):
        """Called by the register just before it moves this order onto a table that has one: Odoo then copies
        every dish this order SENT (served ones too) onto the other order and cancels this one. The open
        tickets go to the other order, in their state, and what Odoo copies is not sent again."""
        self.ensure_one()
        destination = self.browse(destination_id).exists()
        self.check_access("write")
        destination.check_access("write")
        if not destination or destination.config_id != self.config_id or {self.state, destination.state} != {"draft"}:
            raise UserError(_("A table moves onto an open order of the same register."))
        tickets = self.env["bf.kitchen.ticket"].sudo().search(
            [("order_id", "=", self.id), ("state", "in", ("new", "progress", "ready"))])
        tickets.write(destination._bf_kitchen_ticket_vals() | {"general_note": False})
        sent = defaultdict(float)
        for line in _sent(self.last_order_preparation_change)[0].values():
            if line.get("product_id") and line.get("quantity"):
                sent[(line["product_id"], _note(line))] += line["quantity"]
        self.env["bf.kitchen.transfer"].sudo().create([
            {"order_id": destination.id, "product_id": product_id, "note": note or False, "qty": qty}
            for (product_id, note), qty in sent.items()])
        return True

    def _bf_kitchen_absorb_moved(self, changes):
        """What Odoo copies onto this order with a table moved onto it (bf_kitchen_transfer) was sent already:
        it is not sent again. Taken from the first press that brings dishes; what is left then is released
        (and anything older than a few minutes), so that it never swallows a dish ordered later."""
        self.ensure_one()
        Transfer = self.env["bf.kitchen.transfer"].sudo()
        Transfer.search([("create_date", "<", fields.Datetime.now() - timedelta(minutes=10))]).unlink()
        waiting = Transfer.search([("order_id", "=", self.id)], order="id")
        if not waiting or not any(qty > 0 for _line, qty, _removed in changes):
            return changes
        left = []
        for line, qty, removed in changes:
            for moved in waiting:
                if qty > 0 and moved.qty > 0 and moved.product_id.id == line.get("product_id") \
                        and (moved.note or "") == _note(line):
                    taken = min(qty, moved.qty)
                    moved.qty -= taken
                    qty -= taken
            if float_compare(abs(qty), 0.0, precision_digits=PRECISION) > 0:
                left.append((line, qty, removed))
        waiting.unlink()
        return left

    # --- an order cancelled after it was sent
    def action_pos_order_cancel(self):
        """The register deletes an order by cancelling it (Odoo 18 keeps it, cancelled): its open tickets
        show as cancelled, for the cook to decide on."""
        self._bf_kitchen_cancel_tickets(self.filtered(lambda o: o.state in ("draft", "cancel")))
        return super().action_pos_order_cancel()

    def unlink(self):
        self._bf_kitchen_cancel_tickets(self)
        return super().unlink()

    def _bf_kitchen_cancel_tickets(self, orders):
        if orders:
            self.env["bf.kitchen.ticket"].sudo().search(
                [("order_id", "in", orders.ids), ("state", "in", ("new", "progress", "ready"))])._bf_cancel("deleted")
