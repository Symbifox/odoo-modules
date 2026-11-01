from odoo import api, fields, models

from .seating import without_notes


class PosOrder(models.Model):
    _inherit = "pos.order"

    bf_host_seating_id = fields.Many2one("bf.host.seating", "Seating", readonly=True, index=True,
                                         ondelete="set null", copy=False)

    @api.model
    def _process_order(self, order, existing_order):
        order_id = super()._process_order(order, existing_order)
        record = self.browse(order_id)
        if record.config_id.bf_host:
            self.env["bf.host.seating"]._bf_follow_orders(record)
        return order_id

    def action_pos_order_paid(self):
        # Also a payment made from the back office (Odoo's Payment button on the order).
        result = super().action_pos_order_paid()
        self.env["bf.host.seating"]._bf_follow_orders(self.filtered("bf_host_seating_id"))
        return result

    def _bf_host_notes(self):
        """The host's notes this order may carry: its seating's, its refunded order's, or the party's seated at
        its table (an order paid at once is linked to its seating only after its payment)."""
        self.ensure_one()
        Seating = self.env["bf.host.seating"].sudo()
        seatings = (self.bf_host_seating_id | self.sudo().lines.refunded_orderline_id.order_id.bf_host_seating_id)
        if not seatings and self.table_id:
            seatings = Seating.search([("config_id", "=", self.config_id.id),
                                       ("table_id", "in", Seating._bf_group(self.table_id).ids),
                                       ("state", "in", ("seated", "ordered", "to_clear"))])
        return seatings._bf_notes()

    def _prepare_invoice_lines(self):
        """An invoice is kept for years and cannot be erased: the host's notes (an allergy) copied to the order
        stay off it, whenever the invoice is made (at the register, after the payment, from the back office)."""
        lines = super()._prepare_invoice_lines()
        if not self.config_id.bf_host:
            return lines
        notes = self._bf_host_notes()
        if not notes:
            return lines
        kept = []
        for command in lines:
            vals = command[2] if isinstance(command, (list, tuple)) and len(command) > 2 else None
            if isinstance(vals, dict) and vals.get("display_type") == "line_note":
                text = without_notes(vals.get("name"), notes)
                if text is not None:
                    if not text:
                        continue
                    vals["name"] = text
            kept.append(command)
        return kept

    def _prepare_refund_values(self, current_session):
        values = super()._prepare_refund_values(current_session)
        if self.config_id.bf_host and self.general_note:
            text = without_notes(self.general_note, self._bf_host_notes())
            if text is not None:
                values["general_note"] = text or False
        return values

    def action_pos_order_cancel(self):
        result = super().action_pos_order_cancel()
        self.env["bf.host.seating"]._bf_follow_orders(self.filtered("bf_host_seating_id"))
        return result

    def unlink(self):
        seatings = self.sudo().bf_host_seating_id
        result = super().unlink()
        # An order deleted: its seating, without any order left, is a party gone.
        gone = seatings.exists().filtered(lambda s: s.state in ("seated", "ordered", "to_clear")
                                          and not s.order_ids.filtered(lambda o: o.state != "cancel"))
        gone.filtered(lambda s: s.state != "seated").write({"state": "left", "cleared_at": fields.Datetime.now()})
        return result
