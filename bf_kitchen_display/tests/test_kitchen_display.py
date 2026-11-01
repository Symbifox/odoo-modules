"""The kitchen screen: what Order sends becomes tickets, station by station.

Fixtures: bf_kitchen_pos's TestKitchenPos (a dish of salmon, cream sauce and salt; a beer), on a
restaurant register with two floors' worth of tables. The register's synchronisation is driven through
`_process_order`, as `sync_from_ui` does, with the preparation changes the register would send (Odoo 18
keys a line by its uuid and rewrites its note in place); an order is deleted by `action_pos_order_cancel`, as
the register does."""
import json
from uuid import uuid4
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_kitchen_pos.tests.test_kitchen_pos import TestKitchenPos


@tagged("post_install", "-at_install")
class TestKitchenDisplay(TestKitchenPos):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config.module_pos_restaurant = True
        cls.floor = cls.env["restaurant.floor"].create({"name": "Salle", "pos_config_ids": [Command.set(cls.config.ids)]})
        cls.t1, cls.t2, cls.t3 = (cls.env["restaurant.table"].create(
            {"floor_id": cls.floor.id, "table_number": n, "seats": 4}) for n in (1, 2, 3))
        cls.cat_hot = cls.env["pos.category"].create({"name": "Plats chauds"})
        cls.cat_bar = cls.env["pos.category"].create({"name": "Bar"})
        cls.dish.pos_categ_ids = [Command.set(cls.cat_hot.ids)]
        cls.beer.pos_categ_ids = [Command.set(cls.cat_bar.ids)]
        cls.hot = cls.env["bf.kitchen.station"].create({
            "name": "Chaud", "pos_config_ids": [Command.set(cls.config.ids)], "pos_categ_ids": [Command.set(cls.cat_hot.ids)]})
        cls.bar = cls.env["bf.kitchen.station"].create({
            "name": "Bar", "pos_config_ids": [Command.set(cls.config.ids)], "pos_categ_ids": [Command.set(cls.cat_bar.ids)]})
        cls.cook = cls.env["res.users"].create({
            "name": "Cuisinier écran", "login": "kd_cook", "company_id": cls.company.id,
            "company_ids": [Command.set(cls.company.ids)],
            "groups_id": [Command.set([cls.env.ref("bf_kitchen.group_kitchen_cook").id])]})

    # --- helpers: what the register sends
    def draft(self, table, lines):
        return self.env["pos.order"].create({
            "company_id": self.company.id, "session_id": self.session.id, "table_id": table.id,
            "customer_count": 2, "amount_total": 0, "amount_tax": 0, "amount_paid": 0, "amount_return": 0,
            "lines": [Command.create({"name": p.name, "product_id": p.id, "price_unit": p.lst_price, "qty": q,
                                      "price_subtotal": 0, "price_subtotal_incl": 0, "uuid": str(uuid4())})
                      for p, q in lines]})

    def uuid(self, order, product):
        return order.lines.filtered(lambda l: l.product_id == product)[:1].uuid

    def sent(self, order, lines, note=""):
        """The preparation changes after a press of Order: {line uuid: line} for (product, qty, note)."""
        return json.dumps({
            "lines": {self.uuid(order, p): {
                "uuid": self.uuid(order, p), "product_id": p.id, "name": p.name, "note": n, "quantity": q}
                for p, q, n in lines},
            "generalNote": note, "sittingMode": "dine in",
            "metadata": {"serverDate": fields.Datetime.to_string(fields.Datetime.now())}})

    def payload(self, order, lines, note="", **extra):
        return dict({"session_id": self.session.id, "state": "draft", "lines": [], "payment_ids": [],
                     "uuid": order.uuid, "access_token": order.access_token,
                     "amount_paid": 0, "amount_return": 0, "amount_tax": 0, "amount_total": order.amount_total,
                     "last_order_preparation_change": self.sent(order, lines, note)}, **extra)

    def press_order(self, order, lines, note="", **extra):
        self.env["pos.order"]._process_order(self.payload(order, lines, note, **extra), order)
        return self.env["bf.kitchen.ticket"].search([("order_id", "=", order.id)])

    def delete(self, order):
        """What the register does to delete an order (deleteOrders)."""
        order.action_pos_order_cancel()

    def screen(self, user=None, station=False):
        return [t["id"] for t in self.env["bf.kitchen.ticket"].with_user(user or self.cook).bf_display_data(station)["tickets"]]

    # --- sending
    def test_d1_order_reaches_its_stations(self):
        """Order sends the dishes to the hot line and the beer to the bar, with table, covers, notes and
        allergens; the stock does not move."""
        order = self.draft(self.t1, [(self.dish, 2), (self.beer, 1)])
        salmon_before = self.on_stock(self.salmon)
        tickets = self.press_order(order, [(self.dish, 2, "Sans sauce"), (self.beer, 1, "")], note="Anniversaire")
        self.assertEqual(len(tickets), 2)
        hot = tickets.filtered(lambda t: t.station_id == self.hot)
        self.assertEqual(hot.line_ids.mapped("qty"), [2.0])
        self.assertEqual(hot.line_ids.note, "Sans sauce")
        self.assertIn(self.env.ref("bf_food_allergen.allergen_milk").name, hot.line_ids.allergens or "")
        self.assertEqual((hot.table_name, hot.customer_count, hot.general_note), ("Salle 1", 2, "Anniversaire"))
        self.assertEqual(tickets.filtered(lambda t: t.station_id == self.bar).line_ids.product_id, self.beer)
        self.assertEqual(self.on_stock(self.salmon), salmon_before, "the screen moves no stock")

    def test_d2_only_what_changed(self):
        """A second press sends only what was added; the same state synchronised again sends nothing."""
        order = self.draft(self.t1, [(self.dish, 3)])
        self.press_order(order, [(self.dish, 1, "")])
        tickets = self.press_order(order, [(self.dish, 3, "")])
        self.assertEqual(sorted(tickets.line_ids.mapped("qty")), [1.0, 2.0])
        self.assertEqual(len(self.press_order(order, [(self.dish, 3, "")])), 2, "nothing new")

    def test_d3_a_note_changed_reaches_the_kitchen(self):
        """Odoo keeps a line's key and rewrites its note: a note added after the dish was sent
        reaches the kitchen, the old one struck out; it is not a loss to decide."""
        order = self.draft(self.t1, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        last = self.press_order(order, [(self.dish, 1, "Allergie arachides")]).sorted("id")[-1]
        self.assertEqual(last.state, "new")
        self.assertEqual(sorted(last.line_ids.mapped("qty")), [-1.0, 1.0])
        self.assertEqual(last.line_ids.filtered(lambda l: l.qty > 0).note, "Allergie arachides")

    def test_d4_general_note_changed(self):
        """The order's note changed after it was sent: the open tickets show it, marked."""
        order = self.draft(self.t1, [(self.dish, 1)])
        ticket = self.press_order(order, [(self.dish, 1, "")])
        self.press_order(order, [(self.dish, 1, "")], note="Client allergique aux noix")
        self.assertEqual((ticket.general_note, ticket.note_updated), ("Client allergique aux noix", True))

    def test_d5_sub_categories_and_combos(self):
        """[R1, I6, m1] A dish of a sub-category reaches its parent's station; a combo's own line is not a dish."""
        grill = self.env["pos.category"].create({"name": "Grillades", "parent_id": self.cat_hot.id})
        self.dish.pos_categ_ids = [Command.set(grill.ids)]
        order = self.draft(self.t1, [(self.dish, 1)])
        self.assertEqual(self.press_order(order, [(self.dish, 1, "")]).station_id, self.hot)
        choice = self.env["product.combo"].create({"name": "Plat du menu", "combo_item_ids": [
            Command.create({"product_id": self.dish.id, "extra_price": 0})]})
        menu = self.env["product.product"].create({"name": "Menu essai", "type": "combo", "available_in_pos": True,
                                                   "combo_ids": [Command.set(choice.ids)],
                                                   "pos_categ_ids": [Command.set(self.cat_hot.ids)]})
        order2 = self.draft(self.t2, [(menu, 1), (self.dish, 1)])
        tickets = self.press_order(order2, [(menu, 1, ""), (self.dish, 1, "")])
        self.assertEqual(tickets.line_ids.product_id, self.dish)

    # --- tables
    def test_t1_table_moved_to_a_free_one(self):
        """Moved to a free table, the order keeps its tickets, now on the new table."""
        order = self.draft(self.t1, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        tickets = self.press_order(order, [(self.dish, 1, "")], table_id=self.t3.id)
        self.assertEqual(tickets.table_name, "Salle 3")

    def test_t2_table_moved_onto_another_order(self):
        """Moved onto a table that has an order: the register calls bf_kitchen_transfer, Odoo
        cancels the first order and sends the second with the dishes copied. The ticket follows, in its
        state; the dishes are neither cancelled nor sent twice."""
        first = self.draft(self.t1, [(self.dish, 2)])
        ticket = self.press_order(first, [(self.dish, 2, "")])
        ticket.with_user(self.cook).bf_advance()
        second = self.draft(self.t2, [(self.beer, 1)])
        self.press_order(second, [(self.beer, 1, "")])
        first.bf_kitchen_transfer(second.id)
        self.delete(first)
        self.assertEqual((ticket.state, ticket.order_id, ticket.table_name), ("progress", second, "Salle 2"))
        second.write({"lines": [Command.create({"name": self.dish.name, "product_id": self.dish.id, "qty": 2,
                                                "price_unit": 1, "price_subtotal": 0, "price_subtotal_incl": 0,
                                                "uuid": str(uuid4())})]})
        tickets = self.press_order(second, [(self.beer, 1, ""), (self.dish, 2, "")])
        self.assertEqual(len(tickets), 2, "no new ticket for the salmon")
        self.press_order(second, [(self.beer, 1, ""), (self.dish, 3, "")])
        self.assertEqual(self.env["bf.kitchen.ticket"].search([("order_id", "=", second.id)]).sorted("id")[-1].line_ids.qty,
                         1.0, "a dish added afterwards is sent")

    def test_t3_transfer_refused_across_registers(self):
        """bf_kitchen_transfer moves only between open orders of the same register."""
        first = self.draft(self.t1, [(self.dish, 1)])
        self.press_order(first, [(self.dish, 1, "")])
        paid = self.sell([(self.beer, 1)])
        with self.assertRaises(UserError):
            first.bf_kitchen_transfer(paid.id)

    # --- the cook
    def test_k1_the_cook_moves_a_ticket_along(self):
        """To do → in progress → ready → served, each time stamped; back one step; served leaves the screen."""
        order = self.draft(self.t1, [(self.dish, 1)])
        ticket = self.press_order(order, [(self.dish, 1, "")]).with_user(self.cook)
        ticket.bf_advance()
        ticket.bf_advance()
        self.assertEqual(ticket.state, "ready")
        self.assertTrue(ticket.ready_at)
        ticket.bf_back()
        self.assertEqual((ticket.state, ticket.ready_at), ("progress", False))
        ticket.bf_advance()
        ticket.bf_advance()
        self.assertEqual(ticket.state, "served")
        with self.assertRaises(UserError):
            ticket.bf_advance()
        self.assertNotIn(ticket.id, self.screen(station=self.hot.id))

    def test_k2_the_screen_is_the_kitchen_s(self):
        """A cashier neither reads the screen nor moves a ticket; a cook of another company
        neither sees it nor acts on it."""
        order = self.draft(self.t1, [(self.dish, 1)])
        ticket = self.press_order(order, [(self.dish, 1, "")])
        cashier = self.env["res.users"].create({
            "name": "Caisse écran", "login": "kd_cashier", "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "groups_id": [Command.set([self.env.ref("base.group_user").id, self.env.ref("point_of_sale.group_pos_user").id])]})
        with self.assertRaises(AccessError):
            self.env["bf.kitchen.ticket"].with_user(cashier).bf_display_data()
        with self.assertRaises(AccessError):
            ticket.with_user(cashier).bf_advance()
        other = self.env["res.company"].create({"name": "Autre cuisine"})
        stranger = self.env["res.users"].create({
            "name": "Cuisinier ailleurs", "login": "kd_stranger", "company_id": other.id,
            "company_ids": [Command.set(other.ids)],
            "groups_id": [Command.set([self.env.ref("bf_kitchen.group_kitchen_cook").id])]})
        self.assertNotIn(ticket.id, self.screen(stranger))
        for method in ("bf_advance", "bf_back", "bf_no_loss", "bf_declare_loss"):
            with self.assertRaises(AccessError):
                getattr(ticket.with_user(stranger), method)()
        self.assertEqual(ticket.state, "new")

    def test_k3_no_station_no_ticket(self):
        """A register no station shows sends nothing; a dish of no station's category is not sent."""
        (self.hot | self.bar).write({"pos_config_ids": [Command.clear()]})
        self.config.invalidate_recordset(["bf_kitchen_display", "bf_kitchen_station_ids"])
        order = self.draft(self.t1, [(self.dish, 1)])
        self.assertFalse(self.press_order(order, [(self.dish, 1, "")]))
        self.hot.pos_config_ids = [Command.set(self.config.ids)]
        self.config.invalidate_recordset(["bf_kitchen_display", "bf_kitchen_station_ids"])
        order2 = self.draft(self.t2, [(self.beer, 1)])
        self.assertFalse(self.press_order(order2, [(self.beer, 1, "")]), "the bar is no longer on this register")

    # --- cancellations and losses (5c)
    def test_c1_loss_of_a_deleted_order(self):
        """An order deleted after it was sent (cancelled, as the register does): its tickets are
        cancelled, to decide; the cook declares the loss, the dishes leave as their ingredients, reason
        Cancelled after it was sent; decided once."""
        order = self.draft(self.t1, [(self.dish, 2)])
        ticket = self.press_order(order, [(self.dish, 2, "")])
        self.delete(order)
        self.assertEqual((ticket.state, ticket.cancel_reason), ("cancelled", "deleted"))
        self.assertIn(ticket.id, self.screen())
        salmon = self.on_stock(self.salmon)
        ticket.with_user(self.cook).bf_declare_loss()
        self.assertEqual((ticket.loss_decision, ticket.acknowledged), ("loss", True))
        self.assertEqual((ticket.scrap_ids.product_id, ticket.scrap_ids.state), (self.dish, "done"))
        self.assertEqual(ticket.scrap_ids.scrap_reason_tag_ids, self.env.ref("bf_kitchen_display.scrap_reason_cancelled_after_send"))
        self.assertAlmostEqual(self.on_stock(self.salmon), salmon - 480.0, msg="two dishes of 240 g of salmon")
        self.assertNotIn(ticket.id, self.screen())
        ticket.with_user(self.cook).bf_declare_loss()
        self.assertEqual(len(ticket.scrap_ids), 1, "decided once")

    def test_c2_dishes_taken_off(self):
        """Dishes taken off after they were sent make their own cancellation, even pressed with a
        dish added; not started, no loss; started, a loss."""
        order = self.draft(self.t1, [(self.dish, 2), (self.beer, 1)])
        self.press_order(order, [(self.dish, 2, "")])
        tickets = self.press_order(order, [(self.dish, 1, ""), (self.beer, 1, "")])
        off = tickets.filtered(lambda t: t.cancel_reason == "taken_off")
        self.assertEqual((off.state, off.line_ids.qty), ("cancelled", -1.0))
        self.assertEqual(tickets.filtered(lambda t: t.station_id == self.bar).state, "new")
        salmon = self.on_stock(self.salmon)
        off.with_user(self.cook).bf_no_loss()
        self.assertEqual((off.loss_decision, off.scrap_ids), ("none", self.env["stock.scrap"]))
        off2 = self.press_order(order, [(self.beer, 1, "")]).filtered(lambda t: t.cancel_reason == "taken_off" and not t.acknowledged)
        off2.with_user(self.cook).bf_declare_loss()
        self.assertAlmostEqual(self.on_stock(self.salmon), salmon - 240.0)

    def test_c3_one_loss_for_two_stations(self):
        """Two stations take the same dish (a pass with every dish): a deleted order makes two
        cancellations, decided together, one loss."""
        self.env["bf.kitchen.station"].create({"name": "Passe", "pos_config_ids": [Command.set(self.config.ids)]})
        self.config.invalidate_recordset(["bf_kitchen_display", "bf_kitchen_station_ids"])
        order = self.draft(self.t1, [(self.dish, 1)])
        tickets = self.press_order(order, [(self.dish, 1, "")])
        self.assertEqual(len(tickets), 2)
        self.delete(order)
        salmon = self.on_stock(self.salmon)
        tickets[0].with_user(self.cook).bf_declare_loss()
        self.assertEqual(set(tickets.mapped("acknowledged")), {True})
        self.assertAlmostEqual(self.on_stock(self.salmon), salmon - 240.0, msg="one dish, one loss")

    def test_c4_the_loss_is_the_kitchen_s(self):
        """A cashier does not declare the loss of a cancelled ticket."""
        order = self.draft(self.t1, [(self.dish, 1)])
        ticket = self.press_order(order, [(self.dish, 1, "")])
        self.delete(order)
        cashier = self.env["res.users"].create({
            "name": "Caisse perte", "login": "kd_cashier_loss", "company_id": self.company.id,
            "company_ids": [Command.set(self.company.ids)],
            "groups_id": [Command.set([self.env.ref("base.group_user").id, self.env.ref("point_of_sale.group_pos_user").id])]})
        with self.assertRaises(AccessError):
            ticket.with_user(cashier).bf_declare_loss()
        self.assertFalse(ticket.scrap_ids)

    # --- bill splits, table moves, decisions, services
    def test_r2_bill_split(self):
        """A bill split moves dishes already sent (served here) to a new order, both sent in one call
        marked by the register: no cancellation to decide, no ticket to cook again."""
        order = self.draft(self.t1, [(self.dish, 2), (self.beer, 1)])
        tickets = self.press_order(order, [(self.dish, 2, ""), (self.beer, 1, "")])
        for t in tickets.with_user(self.cook):
            t.bf_advance(); t.bf_advance(); t.bf_advance()
        part = self.draft(self.t1, [(self.dish, 1), (self.beer, 1)])
        ctx = {"bf_kitchen_split": [order.uuid, part.uuid]}
        self.env["pos.order"].with_context(**ctx)._process_order(dict(self.payload(order, [(self.dish, 1, "")])), order)
        self.env["pos.order"].with_context(**ctx)._process_order(dict(self.payload(part, [(self.dish, 1, ""), (self.beer, 1, "")])), part)
        after = self.env["bf.kitchen.ticket"].search([("order_id", "in", (order | part).ids)])
        self.assertEqual(after, tickets, "nothing new, nothing cancelled")
        self.assertEqual(set(after.mapped("state")), {"served"})

    def test_r2_transfer_counts_what_was_sent(self):
        """A table moved onto another order: what Odoo copies is what the first order SENT, served
        dishes too, a lowered quantity at its new count; nothing is left waiting to swallow a dish ordered later."""
        first = self.draft(self.t1, [(self.dish, 2), (self.beer, 1)])
        tickets = self.press_order(first, [(self.dish, 2, ""), (self.beer, 1, "")])
        bar = tickets.filtered(lambda t: t.station_id == self.bar).with_user(self.cook)
        bar.bf_advance(); bar.bf_advance(); bar.bf_advance()  # the beer is served
        self.press_order(first, [(self.dish, 1, ""), (self.beer, 1, "")])  # one dish taken off
        second = self.draft(self.t2, [(self.dish, 1), (self.beer, 1)])
        first.bf_kitchen_transfer(second.id)
        self.delete(first)
        self.press_order(second, [(self.dish, 1, ""), (self.beer, 1, "")])  # Odoo's copy
        on_second = self.env["bf.kitchen.ticket"].search([("order_id", "=", second.id)])
        self.assertFalse(on_second.filtered(lambda t: t.state == "new" and t.line_ids.product_id == self.beer),
                         "the served beer is not cooked again")
        self.assertFalse(self.env["bf.kitchen.transfer"].sudo().search([("order_id", "=", second.id)]), "released")
        before = len(on_second)
        self.press_order(second, [(self.dish, 2, ""), (self.beer, 1, "")])
        self.assertEqual(len(self.env["bf.kitchen.ticket"].search([("order_id", "=", second.id)])), before + 1,
                         "a dish ordered later is sent")

    def test_r2_siblings_are_one_press(self):
        """Two presses of the same dish are two decisions; a loss on one throws its dishes only."""
        self.env["bf.kitchen.station"].create({"name": "Passe", "pos_config_ids": [Command.set(self.config.ids)]})
        self.config.invalidate_recordset(["bf_kitchen_display", "bf_kitchen_station_ids"])
        order = self.draft(self.t1, [(self.dish, 2)])
        first = self.press_order(order, [(self.dish, 1, "")])
        second = self.press_order(order, [(self.dish, 2, "")]) - first
        self.delete(order)
        salmon = self.on_stock(self.salmon)
        first[0].with_user(self.cook).bf_declare_loss()
        self.assertEqual(set(first.mapped("loss_decision")), {"loss"}, "the other station's ticket of that press")
        self.assertEqual(set(second.mapped("acknowledged")), {False}, "the other press waits")
        self.assertAlmostEqual(self.on_stock(self.salmon), salmon - 240.0)
        second[0].with_user(self.cook).bf_no_loss()
        self.assertEqual(set(second.mapped("loss_decision")), {"none"})

    def test_r2_services_are_not_sent(self):
        """A service (a service charge) is not a dish: no station shows it."""
        self.env["bf.kitchen.station"].create({"name": "Passe", "pos_config_ids": [Command.set(self.config.ids)]})
        self.config.invalidate_recordset(["bf_kitchen_display", "bf_kitchen_station_ids"])
        fee = self.env["product.product"].create({"name": "Frais de service", "type": "service", "available_in_pos": True})
        order = self.draft(self.t1, [(fee, 1)])
        self.assertFalse(self.press_order(order, [(fee, 1, "")]))
