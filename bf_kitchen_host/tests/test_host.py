"""The host stand: seatings, sections in turn, waiting list, pace, and the names erased after the service.

Fixtures: bf_kitchen_display's TestKitchenDisplay (a restaurant register, tables 1 to 3 of 4 seats, a hot and
a bar station). The register is driven through `_process_order` as `sync_from_ui` does; a table is
opened by the host's RPC calls as the register makes them."""
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.bf_kitchen_display.tests.test_kitchen_display import TestKitchenDisplay

NOW = "odoo.addons.bf_kitchen_host.models.seating.fields.Datetime.now"


@tagged("post_install", "-at_install")
class TestHost(TestKitchenDisplay):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.t4 = cls.env["restaurant.table"].create({"floor_id": cls.floor.id, "table_number": 4, "seats": 2})
        cls.t5 = cls.env["restaurant.table"].create({"floor_id": cls.floor.id, "table_number": 5, "seats": 8})
        Employee = cls.env["hr.employee"].with_company(cls.company)
        cls.camille, cls.alexis, cls.dominique = (Employee.create({"name": n, "company_id": cls.company.id})
                                                  for n in ("Camille Gagnon", "Alexis Bouchard", "Dominique Côté"))
        cls.config.write({"bf_host": True, "bf_host_employee_ids": [Command.set(cls.dominique.ids)]})
        cls.sec_a = cls.env["bf.host.section"].create({
            "name": "A", "sequence": 1, "config_id": cls.config.id, "employee_id": cls.camille.id,
            "table_ids": [Command.set((cls.t1 | cls.t2 | cls.t4).ids)]})
        cls.sec_b = cls.env["bf.host.section"].create({
            "name": "B", "sequence": 2, "config_id": cls.config.id, "employee_id": cls.alexis.id,
            "table_ids": [Command.set((cls.t3 | cls.t5).ids)]})
        cls.waiter = cls.env["res.users"].create({
            "name": "Caisse salle", "login": "host_pos", "company_id": cls.company.id, "lang": "en_US",
            "company_ids": [Command.set(cls.company.ids)],
            "groups_id": [Command.set([cls.env.ref("base.group_user").id,
                                       cls.env.ref("point_of_sale.group_pos_user").id])]})

    # --- helpers: the register's calls
    @property
    def Seating(self):
        return self.env["bf.host.seating"].with_user(self.waiter).with_context(lang="en_US")

    @property
    def Wait(self):
        return self.env["bf.host.wait"].with_user(self.waiter).with_context(lang="en_US")

    def seat(self, table, covers=2, name="Tremblay", note="", wait=False):
        sid = self.Seating.bf_host_seat(self.config.id, table.id, covers, name, note, self.dominique.id, wait)
        return self.env["bf.host.seating"].browse(sid)

    def state(self, table):
        return self.Seating.bf_host_data(self.config.id)["tables"][table.id]["state"]

    def pay_at_register(self, order, lines, note=""):
        self.env["pos.order"]._process_order(self.payload(order, lines, note, state="paid", amount_paid=order.amount_total,
            payment_ids=[Command.create({"amount": order.amount_total, "payment_method_id": self.cash.id})]), order)

    # --- seating
    def test_h1_the_host_seats_a_party(self):
        """Seated by the host: the table shows seated, with the section's server; it is no longer free."""
        seating = self.seat(self.t1, 4, note="Allergie arachides")
        self.assertEqual((seating.state, seating.employee_id, seating.section_id, seating.host_id),
                         ("seated", self.camille, self.sec_a, self.dominique))
        row = self.Seating.bf_host_data(self.config.id)["tables"][self.t1.id]
        self.assertEqual((row["state"], row["covers"], row["server"], row["note"]),
                         ("seated", 4, "Camille Gagnon", "Allergie arachides"))
        with self.assertRaises(UserError):
            self.seat(self.t1)

    def test_h2_the_server_orders(self):
        """The server's order joins the seating; the register's default of one cover does not erase the host's
        count, a real count does."""
        seating = self.seat(self.t1, 4)
        order = self.draft(self.t1, [(self.dish, 1)])
        order.customer_count = 1
        self.press_order(order, [(self.dish, 1, "")])
        self.assertEqual((order.bf_host_seating_id, seating.state, seating.party_size), (seating, "ordered", 4))
        self.assertTrue(seating.ordered_at)
        self.press_order(order, [(self.dish, 1, "")], customer_count=3)
        self.assertEqual(seating.party_size, 3)

    def test_h3_paid_then_cleared(self):
        """Paid at the register: to clear, the meal's length kept; cleared by the host: the table is free."""
        seated_at = fields.Datetime.now() - timedelta(minutes=50)
        seating = self.seat(self.t1, 2)
        seating.sudo().seated_at = seated_at
        order = self.draft(self.t1, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        self.pay_at_register(order, [(self.dish, 1, "")])
        self.assertEqual(order.state, "paid")
        self.assertEqual(seating.state, "to_clear")
        self.assertEqual(self.state(self.t1), "to_clear")
        self.assertGreaterEqual(seating.duration, 49)
        seating.with_user(self.waiter).bf_host_clear()
        self.assertEqual((seating.state, self.state(self.t1)), ("done", "free"))

    def test_h4_a_server_seats_a_walk_in(self):
        """No host: the first order of a free table makes the seating, with the order's covers."""
        order = self.draft(self.t3, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        seating = order.bf_host_seating_id
        self.assertEqual((seating.state, seating.party_size, seating.section_id, seating.employee_id),
                         ("ordered", 2, self.sec_b, self.alexis))
        self.assertEqual(self.state(self.t3), "ordered")

    def test_h5_left_without_ordering(self):
        """A party seated that leaves before ordering frees the table; with an order open, clearing is refused."""
        seating = self.seat(self.t1)
        seating.with_user(self.waiter).bf_host_clear()
        self.assertEqual((seating.state, self.state(self.t1)), ("left", "free"))
        other = self.seat(self.t2)
        order = self.draft(self.t2, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        with self.assertRaises(UserError):
            other.with_user(self.waiter).bf_host_clear()

    def test_h6_order_deleted(self):
        """The order deleted at the register: the party is gone, the table free."""
        seating = self.seat(self.t1)
        order = self.draft(self.t1, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        self.delete(order)
        self.assertEqual((seating.state, self.state(self.t1)), ("left", "free"))

    def test_h7_moves(self):
        """Moved by the host before ordering, the seating changes table and section; moved by the server with
        its order, it follows the order."""
        seating = self.seat(self.t1)
        seating.with_user(self.waiter).bf_host_move(self.t3.id)
        self.assertEqual((seating.table_id, seating.section_id, seating.employee_id), (self.t3, self.sec_b, self.alexis))
        order = self.draft(self.t3, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        with self.assertRaises(UserError):
            seating.with_user(self.waiter).bf_host_move(self.t2.id)
        order.table_id = self.t2
        self.press_order(order, [(self.dish, 1, "")], table_id=self.t2.id)
        self.assertEqual(seating.table_id, self.t2)
        self.assertEqual((self.state(self.t2), self.state(self.t3)), ("ordered", "free"))

    def test_h8_split_bill(self):
        """A split bill's part joins the seating without changing its covers; to clear once both are paid."""
        seating = self.seat(self.t1, 4)
        first = self.draft(self.t1, [(self.dish, 2)])
        first.customer_count = 4  # the register took the host's covers (host_store.js)
        self.press_order(first, [(self.dish, 2, "")])
        part = self.draft(self.t1, [(self.dish, 1)])
        part.customer_count = 2  # its own count, not the party's
        self.press_order(part, [(self.dish, 1, "")])
        self.assertEqual((part.bf_host_seating_id, seating.party_size), (seating, 4))
        self.pay_at_register(part, [(self.dish, 1, "")])
        self.assertEqual(seating.state, "ordered")
        self.pay_at_register(first, [(self.dish, 2, "")])
        self.assertEqual(seating.state, "to_clear")

    def test_h9_served(self):
        """Every kitchen ticket served: the table shows served."""
        self.seat(self.t1)
        order = self.draft(self.t1, [(self.dish, 1)])
        ticket = self.press_order(order, [(self.dish, 1, "")])
        self.assertEqual(self.state(self.t1), "ordered")
        ticket.sudo().write({"state": "served"})
        self.assertEqual(self.state(self.t1), "served")

    def test_h10_paid_from_the_back_office(self):
        """Paid with Odoo's Payment button: the seating is to clear too."""
        seating = self.seat(self.t1)
        order = self.draft(self.t1, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        self.pay(order)
        self.assertEqual(seating.state, "to_clear")

    # --- sections in turn
    def test_r1_rotation(self):
        """The next section has the fewest covers this service; seating it again at once is a double seating,
        and not its turn."""
        first = self.Seating.bf_host_suggest(self.config.id, 2)
        self.assertEqual(first["table_id"], self.t4.id, "section A first, its smallest table that fits")
        self.seat(self.t4, 2)
        self.assertEqual(self.Seating.bf_host_suggest(self.config.id, 2)["table_id"], self.t3.id)
        warnings = self.Seating.bf_host_suggest(self.config.id, 2, self.t1.id)["warnings"]
        self.assertEqual(len(warnings), 2, warnings)
        self.assertIn("B", warnings[0])
        self.assertIn("Double", warnings[1])
        self.config.bf_host_double_seat_minutes = 0
        self.assertEqual(self.Seating.bf_host_suggest(self.config.id, 2)["table_id"], self.t3.id,
                         "no double seating: the section with fewer covers")

    def test_r2_tables_that_fit(self):
        """A party of 6 goes to the table of 8; a table too small, or much too large, is said."""
        self.assertEqual(self.Seating.bf_host_suggest(self.config.id, 6)["table_id"], self.t5.id)
        small = self.Seating.bf_host_suggest(self.config.id, 4, self.t4.id)["warnings"]
        self.assertTrue(any("seats 2" in w for w in small), small)
        self.assertTrue(any("smaller" in w for w in self.Seating.bf_host_suggest(self.config.id, 2, self.t5.id)["warnings"]))

    def test_r3_server_of_a_section(self):
        """The host changes the server of a section; a host is not a server; a section without server is skipped."""
        self.Seating.bf_host_assign(self.sec_a.id, self.alexis.id)
        self.assertEqual(self.sec_a.employee_id, self.alexis)
        with self.assertRaises(UserError):
            self.Seating.bf_host_assign(self.sec_a.id, self.dominique.id)
        self.Seating.bf_host_assign(self.sec_a.id, False)
        self.assertIn(self.Seating.bf_host_suggest(self.config.id, 2)["table_id"], (self.t3.id, self.t5.id))

    def test_r4_one_section_per_table(self):
        with self.assertRaises(ValidationError):
            self.env["bf.host.section"].create({"name": "C", "config_id": self.config.id,
                                                "table_ids": [Command.set(self.t1.ids)]})

    # --- waiting list and pace
    def test_w1_quote(self):
        """Free table: no wait. All taken: the usual meal length less what is gone, rounded up, plus 5; with
        enough meals of that size at this register, their real length."""
        self.assertEqual(self.Wait.bf_host_wait_quote(self.config.id, 2), 0)
        for table in (self.t1, self.t2, self.t3, self.t4, self.t5):
            self.seat(table, 2).sudo().seated_at = fields.Datetime.now() - timedelta(minutes=20)
        self.assertEqual(self.Wait.bf_host_wait_quote(self.config.id, 2), 45, "60 - 20 = 40, + 5")
        for minutes in (30, 32, 34):
            self.env["bf.host.seating"].sudo().create({
                "config_id": self.config.id, "party_size": 2, "state": "done",
                "seated_at": fields.Datetime.now() - timedelta(days=1, minutes=minutes),
                "paid_at": fields.Datetime.now() - timedelta(days=1)})
        self.assertEqual(self.Wait.bf_host_wait_quote(self.config.id, 2), 20, "32 - 20 = 12 → 15, + 5")
        added = self.Wait.bf_host_wait_add(self.config.id, "Roy", 2)
        self.assertEqual(added["quoted"], 20)
        self.assertEqual(self.Wait.bf_host_wait_quote(self.config.id, 2), 20, "one party ahead: the next table")

    def test_w2_seat_from_the_list(self):
        """Seated from the waiting list: the wait is kept; a party gone is marked; a name is required."""
        added = self.Wait.bf_host_wait_add(self.config.id, "Roy", 3, "Chaise haute")
        wait = self.env["bf.host.wait"].browse(added["id"])
        wait.sudo().arrived_at = fields.Datetime.now() - timedelta(minutes=12)
        seating = self.seat(self.t1, 3, "Roy", wait=wait.id)
        self.assertEqual((wait.state, wait.seating_id, seating.wait_id), ("seated", seating, wait))
        self.assertGreaterEqual(wait.waited_minutes, 12)
        with self.assertRaises(UserError):
            self.seat(self.t2, 3, wait=wait.id)
        other = self.env["bf.host.wait"].browse(self.Wait.bf_host_wait_add(self.config.id, "Côté", 2)["id"])
        other.with_user(self.waiter).bf_host_wait_left()
        self.assertEqual(other.state, "left")
        with self.assertRaises(UserError):
            self.Wait.bf_host_wait_add(self.config.id, "  ", 2)

    def test_w3_pace(self):
        """Covers seated in 15 minutes against the ceiling, and the oldest kitchen ticket against its alert."""
        self.config.bf_host_pace_covers = 8
        pace = self.Seating.bf_host_data(self.config.id)["pace"]
        self.assertEqual((pace["covers"], pace["level"]), (0, "green"))
        self.seat(self.t1, 4)
        self.seat(self.t3, 2)
        self.assertEqual(self.Seating.bf_host_data(self.config.id)["pace"]["level"], "amber")
        self.seat(self.t2, 2)
        self.assertEqual(self.Seating.bf_host_data(self.config.id)["pace"]["level"], "red")
        self.config.bf_host_pace_covers = 0
        order = self.draft(self.t1, [(self.dish, 1)])
        ticket = self.press_order(order, [(self.dish, 1, "")])
        self.assertEqual(self.Seating.bf_host_data(self.config.id)["pace"]["level"], "green")
        ticket.sudo().sent_at = fields.Datetime.now() - timedelta(minutes=25)
        pace = self.Seating.bf_host_data(self.config.id)["pace"]
        self.assertEqual((pace["tickets"], pace["level"]), (1, "red"))
        ticket.sudo().sent_at = fields.Datetime.now() - timedelta(hours=5)
        pace = self.Seating.bf_host_data(self.config.id)["pace"]
        self.assertEqual((pace["tickets"], pace["level"]), (0, "green"), "a ticket forgotten for hours is no backlog")

    # --- Loi 25, access
    def test_p1_names_erased_at_closing(self):
        """The session closed: names and notes are erased, the parties still waiting or seated have left."""
        seating = self.seat(self.t1, 2, "Tremblay", "Allergie arachides")
        wait = self.env["bf.host.wait"].browse(self.Wait.bf_host_wait_add(self.config.id, "Roy", 2, "Terrasse")["id"])
        self.session.action_pos_session_closing_control()
        self.assertEqual(self.session.state, "closed")
        self.assertEqual((seating.guest_name, seating.note, seating.state), (False, False, "left"))
        self.assertEqual((wait.guest_name, wait.note, wait.state), (False, False, "left"))

    def test_p2_names_erased_after_a_day(self):
        """A session never closed: the nightly job erases what is older than a day."""
        seating = self.seat(self.t1, 2, "Tremblay", "Sans gluten")
        seating.sudo().seated_at = fields.Datetime.now() - timedelta(days=2)
        self.env["bf.host.seating"]._cron_bf_purge()
        self.assertEqual((seating.guest_name, seating.note, seating.state), (False, False, "left"))

    def test_p3_access(self):
        """The register's users only, on a register with the host stand on, of their companies."""
        cook = self.cook
        with self.assertRaises(AccessError):
            self.env["bf.host.seating"].with_user(cook).bf_host_data(self.config.id)
        self.config.bf_host = False
        with self.assertRaises(UserError):
            self.Seating.bf_host_data(self.config.id)
        self.config.bf_host = True
        other = self.env["res.company"].sudo().create({"name": "Autre resto"})
        outsider = self.env["res.users"].sudo().create({
            "name": "Caisse ailleurs", "login": "host_out", "company_id": other.id,
            "company_ids": [Command.set(other.ids)],
            "groups_id": [Command.set([self.env.ref("base.group_user").id,
                                       self.env.ref("point_of_sale.group_pos_user").id])]})
        with self.assertRaises(AccessError):
            self.env["bf.host.seating"].with_user(outsider).bf_host_data(self.config.id)

    def test_p4_the_host_is_marked_for_the_register(self):
        """Loaded by the register, the host is marked; a server is not."""
        data = {"pos.config": {"data": [{"id": self.config.id, "group_pos_manager_id":
                                          self.env.ref("point_of_sale.group_pos_manager").id,
                                          "advanced_employee_ids": []}]}}
        employees = {e["id"]: e for e in self.env["hr.employee"].with_user(self.waiter)._load_pos_data(data)["data"]}
        self.assertTrue(employees[self.dominique.id]["_bf_host"])
        self.assertFalse(employees[self.camille.id]["_bf_host"])

    # --- payments, joined tables, copies of the note
    def paid_at_once(self, table, lines, covers=2, **extra):
        """A new order the register sends already paid (paid at once, or synchronised after being offline)."""
        vals = dict({
            "session_id": self.session.id, "state": "paid", "uuid": str(uuid4()), "table_id": table.id,
            "customer_count": covers, "amount_paid": 0, "amount_return": 0, "amount_tax": 0, "amount_total": 0,
            "lines": [Command.create({"name": p.name, "product_id": p.id, "price_unit": p.lst_price, "qty": q,
                                      "price_subtotal": 0, "price_subtotal_incl": 0, "uuid": str(uuid4())})
                      for p, q in lines],
            "payment_ids": [Command.create({"amount": 0, "payment_method_id": self.cash.id})],
        }, **extra)
        return self.env["pos.order"].browse(self.env["pos.order"]._process_order(vals, False))

    def test_l1_note_erased_from_the_order_and_its_tickets(self):
        """The host's note copied to the order reaches the kitchen and stays there through
        the payment; after the service every note the seating had is erased from the seating, the order, what
        was sent and the kitchen tickets, at closing as by the nightly job; the server's own words stay."""
        seating = self.seat(self.t1, 2, "Tremblay", "Allergie noix")
        seating.with_user(self.waiter).bf_host_update(2, "Tremblay", "Allergie arachides")
        order = self.draft(self.t1, [(self.dish, 1)])
        order.general_note = "Allergie noix"  # the register copied the first note (host_store.js)
        ticket = self.press_order(order, [(self.dish, 1, "")], note="Allergie noix")
        ticket.sudo().state = "served"
        self.pay_at_register(order, [(self.dish, 1, "")], note="Allergie noix")
        self.assertEqual((order.state, order.general_note, ticket.general_note), ("paid", "Allergie noix", "Allergie noix"))
        self.assertIn("Allergie noix", order.last_order_preparation_change)
        seating.sudo().seated_at = fields.Datetime.now() - timedelta(days=2)
        self.env["bf.host.seating"]._cron_bf_purge()
        self.assertEqual((seating.guest_name, seating.note, seating.sudo().note_history), (False, False, False))
        self.assertFalse(order.general_note)
        self.assertNotIn("Allergie", order.last_order_preparation_change)
        self.assertFalse(ticket.general_note)
        other = self.seat(self.t2, 2, "Roy", "Vin")
        order2 = self.draft(self.t2, [(self.dish, 1)])
        order2.general_note = "Vinaigrette à part, Vin"
        self.press_order(order2, [(self.dish, 1, "")], note="Vinaigrette à part, Vin")
        self.pay_at_register(order2, [(self.dish, 1, "")], note="Vinaigrette à part, Vin")
        self.session.sudo().state = "closed"
        self.session._bf_host_close()
        self.assertEqual((other.note, order2.general_note), (False, "Vinaigrette à part"), "whole words only")

    def test_l2_never_on_an_invoice(self):
        """An invoice made after the payment (the register's Invoice button, or the back office) does
        not carry the host's note; the order keeps it until the purge."""
        self.seat(self.t1, 2, "Tremblay", "Allergie arachides")
        order = self.draft(self.t1, [(self.dish, 1)])
        order.general_note = "Allergie arachides"
        self.press_order(order, [(self.dish, 1, "")], note="Allergie arachides")
        self.pay_at_register(order, [(self.dish, 1, "")], note="Allergie arachides")
        order.partner_id = self.env["res.partner"].create({"name": "Entreprise du client"})
        order.with_context(generate_pdf=False).action_pos_order_invoice()
        self.assertTrue(order.account_move)
        notes = order.account_move.invoice_line_ids.filtered(lambda l: l.display_type == "line_note").mapped("name")
        self.assertFalse([n for n in notes if "Allergie" in (n or "")], notes)
        self.assertEqual(order.general_note, "Allergie arachides")

    def test_l3_paid_at_once(self):
        """Paid at once at a table the host seated: linked, to clear, the table no longer blocked;
        a walk-in paid at once gets its seating, to clear."""
        seating = self.seat(self.t1, 3)
        order = self.paid_at_once(self.t1, [(self.beer, 2)], covers=3)
        self.assertEqual(order.state, "paid")
        self.assertEqual((order.bf_host_seating_id, seating.state), (seating, "to_clear"))
        self.assertTrue(seating.ordered_at and seating.paid_at)
        walk_in = self.paid_at_once(self.t3, [(self.beer, 1)])
        self.assertEqual(walk_in.bf_host_seating_id.state, "to_clear")
        self.assertEqual(self.state(self.t3), "to_clear")

    def test_l4_joined_tables(self):
        """A party seated at a table later joined to another: the group shows it, refuses a second
        party, takes the order at the head table; the seats of joined tables add up."""
        roy = self.seat(self.t2, 2)
        self.t2.parent_id = self.t1
        self.assertEqual((self.state(self.t1), self.state(self.t2)), ("seated", "seated"))
        with self.assertRaises(UserError):
            self.seat(self.t1, 2)
        order = self.draft(self.t1, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        self.assertEqual((order.bf_host_seating_id, roy.state), (roy, "ordered"))
        self.t3.parent_id = self.t5  # 4 + 8 seats
        warnings = self.Seating.bf_host_suggest(self.config.id, 10, self.t5.id)["warnings"]
        self.assertFalse(any("seats" in w for w in warnings), warnings)

    def test_l5_moved_onto_a_party_without_order(self):
        """An order moved onto a table where a party sits without an order: one party at that table."""
        empty = self.seat(self.t3, 2)
        moving = self.seat(self.t1, 2)
        order = self.draft(self.t1, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        order.table_id = self.t3
        self.press_order(order, [(self.dish, 1, "")], table_id=self.t3.id)
        self.assertEqual((moving.table_id, empty.state), (self.t3, "left"))
        self.assertEqual(self.Seating.bf_host_data(self.config.id)["tables"][self.t3.id]["seating_id"], moving.id)

    def test_l6_limits(self):
        """Covers between 1 and 99; a table cleared twice says so."""
        with self.assertRaises(UserError):
            self.seat(self.t1, 100)
        seating = self.seat(self.t1, 2)
        with self.assertRaises(UserError):
            seating.with_user(self.waiter).bf_host_update(0)
        seating.with_user(self.waiter).bf_host_clear()
        with self.assertRaisesRegex(UserError, "already cleared"):
            seating.with_user(self.waiter).with_context(lang="en_US").bf_host_clear()

    def test_l7_a_new_server_starts_at_zero(self):
        """The rotation counts the covers of the section's server now: a server taking over a section
        does not inherit the covers seated before."""
        self.seat(self.t1, 4)
        data = self.Seating.bf_host_data(self.config.id)
        self.assertEqual(next(s for s in data["sections"] if s["id"] == self.sec_a.id)["covers"], 4)
        self.Seating.bf_host_assign(self.sec_a.id, self.alexis.id)
        data = self.Seating.bf_host_data(self.config.id)
        section = next(s for s in data["sections"] if s["id"] == self.sec_a.id)
        self.assertEqual((section["covers"], section["open"]), (0, 1))

    def test_l8_copies_of_the_note(self):
        """A refund does not copy the host's note; an order reserved without a dish is linked to the
        party seated there, and its note erased with the others."""
        seating = self.seat(self.t1, 2, "Tremblay", "Allergie arachides")
        order = self.draft(self.t1, [(self.dish, 1)])
        order.general_note = "Allergie arachides"
        self.press_order(order, [(self.dish, 1, "")], note="Allergie arachides")
        self.pay_at_register(order, [(self.dish, 1, "")], note="Allergie arachides")
        refund = self.env["pos.order"].browse(order.refund()["res_id"])
        self.assertFalse(refund.general_note)
        booked = self.seat(self.t2, 3, "Roy", "Sans gluten")
        empty = self.env["pos.order"].create({
            "company_id": self.company.id, "session_id": self.session.id, "table_id": self.t2.id,
            "general_note": "Sans gluten", "amount_total": 0, "amount_tax": 0, "amount_paid": 0, "amount_return": 0})
        self.env["pos.order"]._process_order(self.payload(empty, [], "Sans gluten"), empty)
        self.assertEqual((empty.bf_host_seating_id, booked.state), (booked, "seated"))
        self.delete(empty)  # the party leaves: Odoo cancels the reserved order
        self.assertEqual(booked.state, "left")
        booked.sudo().seated_at = fields.Datetime.now() - timedelta(days=2)
        self.env["bf.host.seating"]._cron_bf_purge()
        self.assertFalse(empty.general_note)

    def test_l9_joined_twice_and_waiting_limits(self):
        """A table joined to a table already joined belongs to the same group; the waiting list
        takes 1 to 99 guests."""
        roy = self.seat(self.t3, 2)
        self.t3.parent_id = self.t2
        self.t2.parent_id = self.t1
        self.assertEqual(self.state(self.t1), "seated")
        with self.assertRaises(UserError):
            self.seat(self.t1, 2)
        order = self.draft(self.t1, [(self.dish, 1)])
        self.press_order(order, [(self.dish, 1, "")])
        self.assertEqual(order.bf_host_seating_id, roy)
        with self.assertRaises(UserError):
            self.Wait.bf_host_wait_add(self.config.id, "Grand groupe", 150)
