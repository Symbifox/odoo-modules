from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

COOK = "bf_kitchen.group_kitchen_cook"
STATES = [("new", "To do"), ("progress", "In progress"), ("ready", "Ready"), ("served", "Served"),
          ("cancelled", "Cancelled")]
NEXT = {"new": "progress", "progress": "ready", "ready": "served"}
STAMP = {"progress": "started_at", "ready": "ready_at", "served": "served_at"}
# A cancellation waits on the screen for the cook's decision (a loss, or not started) this long at most.
SHOW_CANCELLED = timedelta(hours=12)
LOSS_REASON = ("cancelled_after_send", "Cancelled after it was sent", "Annulé après envoi", 70)


class KitchenTicket(models.Model):
    """What one press of Order sent to one station: the dishes added (or taken off) since the last
    one. Made by the register's synchronisation, never typed."""
    _name = "bf.kitchen.ticket"
    _description = "Kitchen ticket"
    _order = "sent_at, id"

    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    config_id = fields.Many2one("pos.config", "Register", readonly=True, index=True)
    session_id = fields.Many2one("pos.session", readonly=True, index=True)
    order_id = fields.Many2one("pos.order", readonly=True, index=True, ondelete="set null")
    order_ref = fields.Char("Order", readonly=True)
    table_id = fields.Many2one("restaurant.table", readonly=True, ondelete="set null")
    table_name = fields.Char("Table", readonly=True)
    customer_count = fields.Integer("Covers", readonly=True)
    sent_by = fields.Char("Sent by", readonly=True)
    station_id = fields.Many2one("bf.kitchen.station", readonly=True, index=True, ondelete="set null")
    sent_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    started_at = fields.Datetime(readonly=True)
    ready_at = fields.Datetime(readonly=True)
    served_at = fields.Datetime(readonly=True)
    cancelled_at = fields.Datetime(readonly=True)
    state = fields.Selection(STATES, required=True, default="new", readonly=True, index=True)
    state_before_cancel = fields.Selection(STATES[:4], readonly=True)
    cancel_reason = fields.Selection(
        [("deleted", "Order deleted after it was sent"), ("taken_off", "Dishes taken off after they were sent")],
        readonly=True)
    acknowledged = fields.Boolean(readonly=True, help="A cook decided on the cancellation.")
    loss_decision = fields.Selection(
        [("loss", "Loss declared"), ("none", "Not started: no loss")], readonly=True)
    scrap_ids = fields.Many2many("stock.scrap", string="Losses", readonly=True)
    sending = fields.Char(readonly=True, index=True, help="The press of Order this ticket came from.")
    general_note = fields.Char("Note", readonly=True)
    note_updated = fields.Boolean(readonly=True, help="The order's note changed after this ticket was sent.")
    line_ids = fields.One2many("bf.kitchen.ticket.line", "ticket_id", readonly=True)

    @api.depends("table_name", "order_ref", "sent_at")
    def _compute_display_name(self):
        for ticket in self:
            ticket.display_name = ticket.table_name or ticket.order_ref or _("Ticket %s", ticket.id)

    # --- the screen
    def _bf_check_cook(self):
        """The kitchen staff only, on tickets of their companies (the methods below then write as sudo)."""
        if not self.env.user.has_group(COOK):
            raise AccessError(_("The kitchen screen is for the kitchen staff."))
        self.check_access("read")

    @api.model
    def bf_display_data(self, station_id=False):
        """The tickets a station shows: those not served, and the cancellations of the last minutes not
        seen yet. Companies of the user only."""
        self._bf_check_cook()
        now = fields.Datetime.now()
        domain = [("company_id", "in", self.env.companies.ids), "|",
                  ("state", "in", ("new", "progress", "ready")),
                  "&", "&", ("state", "=", "cancelled"), ("acknowledged", "=", False),
                  ("cancelled_at", ">=", now - SHOW_CANCELLED)]
        if station_id:
            domain.append(("station_id", "=", int(station_id)))
        tickets = self.sudo().search(domain)
        stations = self.env["bf.kitchen.station"].search([("company_id", "in", self.env.companies.ids)])
        return {
            "now": fields.Datetime.to_string(now),
            "stations": [{"id": s.id, "name": s.name} for s in stations],
            "tickets": [t._bf_card() for t in tickets],
        }

    def _bf_card(self):
        self.ensure_one()
        return {
            "id": self.id,
            "table": self.table_name or "",
            "order": self.order_ref or "",
            "covers": self.customer_count,
            "sent_by": self.sent_by or "",
            "station": self.station_id.name or "",
            "sent_at": fields.Datetime.to_string(self.sent_at),
            "state": self.state,
            "cancel_reason": self.cancel_reason or "",
            "to_decide": self.state == "cancelled" and not self.acknowledged,
            "note": self.general_note or "",
            "note_updated": self.note_updated,
            "lines": [{
                "id": line.id, "name": line.name, "qty": abs(line.qty), "note": line.note or "",
                "allergens": line.allergens or "", "taken_off": line.qty < 0,
            } for line in self.line_ids],
        }

    def bf_advance(self):
        """To do → in progress → ready → served."""
        self._bf_check_cook()
        for ticket in self.sudo():
            if ticket.state not in NEXT:
                raise UserError(_("This ticket is %s.", dict(STATES)[ticket.state].lower()))
            new = NEXT[ticket.state]
            ticket.write({"state": new, STAMP[new]: fields.Datetime.now()})
        return True

    def bf_back(self):
        """Undo a press: back one state (a ticket marked ready by mistake)."""
        self._bf_check_cook()
        back = {v: k for k, v in NEXT.items()}
        for ticket in self.sudo():
            if ticket.state not in back:
                raise UserError(_("This ticket cannot go back."))
            ticket.write({"state": back[ticket.state], STAMP[ticket.state]: False})
        return True

    def _bf_to_decide(self):
        """These cancellations and their siblings: the tickets of the same press of Order on another station
        (two stations take the same dish) are decided at once, so that a dish is never lost twice. Another
        press of the same dish is another decision (one may have been started, not the other)."""
        mine = self.filtered(lambda t: t.state == "cancelled" and not t.acknowledged)
        sendings = set(mine.mapped("sending")) - {False}
        if not sendings:
            return mine
        return mine | self.search([
            ("state", "=", "cancelled"), ("acknowledged", "=", False), ("id", "not in", mine.ids),
            ("company_id", "in", mine.company_id.ids), ("sending", "in", list(sendings))])

    def bf_no_loss(self):
        """Not started: nothing was lost, the cancellation leaves the screen."""
        self._bf_check_cook()
        self.sudo()._bf_to_decide().write({"acknowledged": True, "loss_decision": "none"})
        return True

    def bf_declare_loss(self):
        """Started or ready: the dishes go out as a loss, reason Cancelled after it was sent, at the kitchen
        cost of the day, from the stock of the company's kitchen warehouse (a dish leaves as its
        ingredients, as any kitchen loss)."""
        self._bf_check_cook()
        Scrap = self.env["stock.scrap"].sudo()
        reason = self._bf_loss_reason()
        thrown = set()  # (order, line uuid, sign): one loss per dish, whatever the stations
        for ticket in self.sudo()._bf_to_decide():
            warehouse = ticket.company_id._bf_kitchen_warehouse()
            if not warehouse:
                raise UserError(_("%s has no warehouse to take the loss from.", ticket.company_id.name))
            scraps = Scrap
            for line in ticket.line_ids.filtered(lambda l: l.product_id):
                qty = abs(line.qty)
                if not qty or (ticket.cancel_reason == "deleted") != (line.qty > 0):
                    continue
                key = (ticket.sending or ticket.id, line.line_uuid or line.id, line.qty > 0)
                if key in thrown:
                    continue
                thrown.add(key)
                scraps |= Scrap.with_company(ticket.company_id).create({
                    "product_id": line.product_id.id, "scrap_qty": qty,
                    "product_uom_id": line.product_id.uom_id.id, "location_id": warehouse.lot_stock_id.id,
                    "company_id": ticket.company_id.id, "origin": ticket.order_ref or ticket.table_name,
                    "scrap_reason_tag_ids": [fields.Command.set(reason.ids)]})
            scraps.do_scrap()
            ticket.write({"acknowledged": True, "loss_decision": "loss", "scrap_ids": [fields.Command.set(scraps.ids)]})
        return True

    @api.model
    def _bf_loss_reason(self):
        """The loss reason of a dish cancelled after it was sent: ours, or the database's tag of that name."""
        key, name_en, name_fr, sequence = LOSS_REASON
        tag = self.env.ref("bf_kitchen_display.scrap_reason_%s" % key, raise_if_not_found=False)
        if tag:
            return tag
        Tag = self.env["stock.scrap.reason.tag"].sudo()
        tag = Tag.search([("name", "=", name_en)], limit=1)
        if not tag:
            tag = Tag.create({"name": name_en, "sequence": sequence})
            if self.env["res.lang"]._lang_get("fr_CA"):
                tag.update_field_translations("name", {"fr_CA": name_fr})
        self.env["ir.model.data"].sudo().create({
            "module": "bf_kitchen_display", "name": "scrap_reason_%s" % key, "model": tag._name,
            "res_id": tag.id, "noupdate": True})
        return tag

    def _bf_cancel(self, reason):
        for ticket in self.filtered(lambda t: t.state in NEXT):
            ticket.write({"state_before_cancel": ticket.state, "state": "cancelled",
                          "cancel_reason": reason, "cancelled_at": fields.Datetime.now()})


class KitchenTicketLine(models.Model):
    _name = "bf.kitchen.ticket.line"
    _description = "Kitchen ticket line"
    _order = "ticket_id, id"

    ticket_id = fields.Many2one("bf.kitchen.ticket", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="ticket_id.company_id", store=True, index=True)
    product_id = fields.Many2one("product.product", readonly=True)
    name = fields.Char(required=True, readonly=True)
    qty = fields.Float("Quantity", readonly=True, help="Negative: taken off the order after it was sent.")
    note = fields.Char(readonly=True)
    line_uuid = fields.Char(readonly=True)
    allergens = fields.Char(readonly=True, help="The dish's allergens when it was sent.")


class KitchenTransfer(models.Model):
    """What a table moved onto another order (pos.order.bf_kitchen_transfer) had sent: Odoo copies it onto
    the other order, which must not send it again. Short-lived: released by the next press, or after
    ten minutes."""
    _name = "bf.kitchen.transfer"
    _description = "Kitchen dishes moved with a table"

    order_id = fields.Many2one("pos.order", required=True, index=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade")
    note = fields.Char()
    qty = fields.Float()
