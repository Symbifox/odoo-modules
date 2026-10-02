import re
from datetime import date, datetime, timedelta

import pytz

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSchoolMeal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais", "meal_payment_modes": "choice"})
        year = env["bf.school.year"].create({"name": "2026-2027", "school_id": cls.school.id,
                                             "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        group = env["bf.school.group"].create({"name": "301", "school_id": cls.school.id, "year_id": year.id})
        cls.staff = new_test_user(env, login="school_ml_staff", groups="bf_school_core.group_school_user")
        Partner = env["res.partner"]
        cls.a = Partner.create({"name": "Alpha Essai", "is_student": True})
        cls.b = Partner.create({"name": "Bravo Essai", "is_student": True,
                                "meal_allergen_ids": [(6, 0, env.ref("bf_school_meal.allergen_peanut").ids)]})
        cls.c = Partner.create({"name": "Charlie Essai", "is_student": True})
        for student in (cls.a, cls.b, cls.c):
            env["bf.school.enrollment"].create({"student_id": student.id, "group_id": group.id})
        cls.p = new_test_user(env, login="school_ml_p", groups="base.group_portal", email="p@essai.test")
        cls.p2 = new_test_user(env, login="school_ml_p2", groups="base.group_portal", email="p2@essai.test")
        cls.q = new_test_user(env, login="school_ml_q", groups="base.group_portal", email="q@essai.test")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.a.id, "guardian_id": cls.p2.partner_id.id})
        Link.create({"student_id": cls.a.id, "guardian_id": cls.p.partner_id.id, "is_payer": True})
        Link.create({"student_id": cls.b.id, "guardian_id": cls.p.partner_id.id, "is_payer": True})
        Link.create({"student_id": cls.c.id, "guardian_id": cls.q.partner_id.id, "is_payer": True})
        Item = env["bf.school.meal.item"]
        cls.pasta = Item.create({"name": "Pâtes sauce tomate", "school_id": cls.school.id, "price": 6.5})
        cls.satay = Item.create({"name": "Poulet satay", "school_id": cls.school.id, "price": 7.0,
                                 "allergen_ids": [(6, 0, env.ref("bf_school_meal.allergen_peanut").ids)]})
        cls.today = cls.school._school_now().date()

    def _day(self, offset, items=None):
        day = self.today + timedelta(days=offset)
        return self.env["bf.school.meal.day"].search([("school_id", "=", self.school.id), ("date", "=", day)]) \
            or self.env["bf.school.meal.day"].create({"school_id": self.school.id, "date": day,
                                                      "item_ids": [(6, 0, (items or self.pasta | self.satay).ids)]})

    def _account(self, partner=None):
        return self.env["bf.school.meal.account"]._school_get((partner or self.p).partner_id, self.env.company,
                                                              self.school)

    def _fund(self, amount, partner=None):
        account = self._account(partner)
        self.env["bf.school.meal.entry"].create({"account_id": account.id, "amount": amount,
                                                 "kind": "adjustment", "note": "Essai"})
        return account

    def _place(self, student=None, day=None, item=None, partner=None):
        return self.env["bf.school.meal.order"]._school_place(
            (partner or self.p).partner_id, student or self.a, day or self._day(5), item or self.pasta)

    # Prepaid
    def test_payer_is_the_adult_who_pays(self):
        order = self._fund(20) and self._place()
        self.assertEqual(order.account_id.partner_id, self.p.partner_id)
        self.assertEqual(order.ordered_by_id, self.p.partner_id)

    def test_prepaid_debits_and_credits(self):
        account = self._fund(10)
        order = self._place()
        self.assertEqual((order.mode, order.price, account.balance), ("prepaid", 6.5, 3.5))
        with self.assertRaises(UserError):
            self._place(day=self._day(6))
        self.assertFalse(self.env["bf.school.meal.order"].search([("day_id", "=", self._day(6).id)]),
                         "a refused order leaves nothing behind")
        order._school_cancel_by(self.p.partner_id)
        self.assertEqual(account.balance, 10)
        self.assertEqual(order.state, "cancelled")
        with self.assertRaises(UserError):
            order._school_cancel_by(self.p.partner_id)
        self.assertEqual(account.balance, 10, "credited once")

    def test_price_is_frozen(self):
        self._fund(20)
        order = self._place()
        self.pasta.price = 9
        self.assertEqual(order.price, 6.5)
        with self.assertRaises(UserError):
            order.write({"item_id": self.satay.id})

    def test_topup_paid_credits_once(self):
        account = self._account()
        topup = account._school_topup(50)
        self.assertEqual(account.balance, 0)
        move = topup.invoice_id
        self.assertEqual((move.state, move.amount_total, move.amount_tax), ("posted", 50, 0))
        self.assertFalse(move.invoice_user_id)
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=move.ids).create({})._create_payments()
        self.assertEqual(topup.state, "paid")
        self.assertEqual(account.balance, 50)
        move._invoice_paid_hook()
        self.assertEqual(account.balance, 50, "a second hook does not credit again")
        for amount in (2, 900):
            with self.assertRaises(UserError):
                account._school_topup(amount)

    def test_entries_are_a_register(self):
        account = self._fund(5)
        entry = account.entry_ids
        with self.assertRaises(UserError):
            entry.write({"amount": 500})
        with self.assertRaises(UserError):
            entry.unlink()
        with self.assertRaises(ValidationError):
            self.env["bf.school.meal.entry"].create({"account_id": account.id, "amount": 5, "kind": "adjustment"})

    # Monthly
    def test_monthly_invoice(self):
        account = self._account(self.q)
        account._school_set_mode("monthly", self.school)
        first = self._place(student=self.c, partner=self.q)
        second = self._place(student=self.c, partner=self.q, item=self.satay)
        dropped = self._place(student=self.c, partner=self.q, day=self._day(7))
        dropped.action_cancel()
        self.assertEqual(account.balance, 0, "nothing is taken from a balance")
        month = date(first.date.year, first.date.month, 1)
        move = account._school_invoice_month(month)
        self.assertEqual(move.amount_total, 13.5)
        self.assertEqual(move.amount_tax, 0)
        self.assertFalse(move.invoice_user_id, "the school sends it, not whoever ran the cron")
        self.assertEqual((first.invoice_id, second.invoice_id), (move, move))
        self.assertFalse(dropped.invoice_id)
        self.assertFalse(account._school_invoice_month(month), "invoiced once")
        if self.env["res.lang"]._lang_get("fr_CA"):
            self.q.partner_id.lang = "fr_CA"
            third = self._place(student=self.c, partner=self.q, day=self._day(9))
            later = account._school_invoice_month(date(third.date.year, third.date.month, 1))
            self.assertTrue(later.invoice_origin.startswith("Repas "), "the cron speaks the payer's language")
        with self.assertRaises(UserError):
            first.action_cancel()

    def test_monthly_cron_bills_last_month_even_after_a_change(self):
        d = self.env["res.partner"].create({"name": "Delta Essai", "is_student": True})
        self.env["bf.school.enrollment"].create({"student_id": d.id, "group_id": self.a.student_group_ids.id})
        self.env["bf.school.guardian.link"].create({"student_id": d.id, "guardian_id": self.p2.partner_id.id,
                                                    "is_payer": True})
        self._account(self.p2)._school_set_mode("monthly", self.school)
        still_monthly = self._place(student=d, partner=self.p2)
        account = self._account(self.q)
        account._school_set_mode("monthly", self.school)
        order = self._place(student=self.c, partner=self.q)
        account._school_set_mode("prepaid", self.school)
        self.assertEqual(order.mode, "monthly", "the mode is frozen on the order")
        next_month = (order.date.replace(day=1) + timedelta(days=32)).replace(day=5)
        moves = self.env["bf.school.meal.account"]._cron_monthly_invoices(today=next_month)
        self.assertEqual(len(moves), 2)
        self.assertTrue(order.invoice_id and still_monthly.invoice_id)
        self.assertEqual(order.invoice_id.partner_id, self.q.partner_id)

    def test_mode_rules(self):
        account = self._fund(5, self.q)
        with self.assertRaises(UserError):
            account._school_set_mode("monthly", self.school)  # a balance is left
        self.school.meal_payment_modes = "prepaid"
        fresh = self.env["bf.school.meal.account"]._school_get(self.p2.partner_id, self.env.company, self.school)
        self.assertEqual(fresh.mode, "prepaid")
        with self.assertRaises(UserError):
            fresh._school_set_mode("monthly", self.school)  # empty, but the school decides
        self.school.meal_payment_modes = "monthly"
        other = self.env["bf.school.meal.account"]._school_get(self.staff.partner_id, self.env.company, self.school)
        self.assertEqual(other.mode, "monthly")

    # Rules of the day
    def test_allergy_blocks_the_order(self):
        self._fund(20)
        with self.assertRaises(ValidationError):
            self._place(student=self.b, item=self.satay)
        self.assertEqual(self._place(student=self.b).item_id, self.pasta)

    def test_meal_on_the_menu_once(self):
        self._fund(20)
        self._place()
        with self.assertRaises(ValidationError):
            self._place()
        other = self.env["bf.school.meal.item"].create(
            {"name": "Pas au menu", "school_id": self.school.id, "price": 1})
        with self.assertRaises(ValidationError):
            self._place(item=other)

    def test_deadlines(self):
        self._fund(50)
        with self.assertRaises(UserError):
            self._place(day=self._day(1))
        self._place(day=self._day(2))
        # The office orders late for a family.
        late = self.env["bf.school.meal.order"].create(
            {"student_id": self.a.id, "day_id": self._day(1).id, "item_id": self.pasta.id})
        self.assertEqual(late.state, "ordered")
        tz = pytz.timezone("America/Toronto")
        day = self._day(1)
        before = tz.localize(datetime.combine(day.date, datetime.min.time()).replace(hour=7, minute=59))
        after = before + timedelta(minutes=2)
        self.assertTrue(day._is_cancellable(now=before))
        self.assertFalse(day._is_cancellable(now=after))
        self.assertTrue(self._day(3)._is_orderable(now=tz.localize(datetime.combine(self._day(1).date, datetime.min.time()))))
        self.assertFalse(self._day(3)._is_orderable(now=tz.localize(datetime.combine(self._day(2).date, datetime.min.time()))))

    def test_closed_day_credits_everything(self):
        prepaid = self._fund(20)
        monthly = self._account(self.q)
        monthly._school_set_mode("monthly", self.school)
        day = self._day(5)
        o1 = self._place(day=day)
        o2 = self._place(student=self.c, partner=self.q, day=day)
        day.action_close("Tempête")
        self.assertEqual((o1.state, o2.state), ("credited", "credited"))
        self.assertEqual(prepaid.balance, 20)
        self.assertFalse(monthly._school_invoice_month(date(day.date.year, day.date.month, 1)))
        with self.assertRaises(UserError):
            self._place(day=day)

    def test_family_orders_for_own_children_only(self):
        self._fund(20)
        self._fund(20, self.q)
        with self.assertRaises(UserError):
            self._place(student=self.c)  # q's child
        order = self._place()
        with self.assertRaises(UserError):
            order._school_cancel_by(self.q.partner_id)

    # Staff
    def test_staff_reads_the_kitchen_list_not_the_accounts(self):
        self._fund(20)
        self._place(student=self.b)
        orders = self.env["bf.school.meal.order"].with_user(self.staff).search([("student_id", "=", self.b.id)])
        self.assertEqual(orders.student_allergen_ids.mapped("name"), ["Peanuts"])
        with self.assertRaises(AccessError):
            self.env["bf.school.meal.account"].with_user(self.staff).search([])

    # Portal
    def test_portal_order_and_scope(self):
        self._fund(20)
        day = self._day(5)
        self.authenticate("school_ml_p", "school_ml_p")
        page = self.url_open("/my/school/meals").text
        self.assertIn("Pâtes sauce tomate", page)
        self.assertIn("20.00", page)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        self.url_open("/my/school/meals/order", data={
            "csrf_token": token, "student_id": self.a.id, "day_id": day.id, "item_id": self.pasta.id})
        order = self.env["bf.school.meal.order"].search([("student_id", "=", self.a.id), ("day_id", "=", day.id)])
        self.assertEqual(order.state, "ordered")
        refused = self.url_open("/my/school/meals/order", data={
            "csrf_token": token, "student_id": self.c.id, "day_id": day.id, "item_id": self.pasta.id})
        self.assertEqual(refused.status_code, 404)
        self._fund(20, self.q)
        stranger = self._place(student=self.c, partner=self.q, day=self._day(6))
        response = self.url_open("/my/school/meals/%s/cancel" % stranger.id, data={"csrf_token": token})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(stranger.state, "ordered")
        # Balance too low: refused, shown, and no order left behind.
        low_day = self._day(8)
        self.env["bf.school.meal.entry"].create({"account_id": self._account().id, "amount": -self._account().balance + 1,
                                                 "kind": "adjustment", "note": "Essai"})
        shown = self.url_open("/my/school/meals/order", data={
            "csrf_token": token, "student_id": self.a.id, "day_id": low_day.id, "item_id": self.pasta.id}).text
        self.assertIn("too low", shown, "the refusal is shown after the redirect")
        self.assertFalse(self.env["bf.school.meal.order"].search([("day_id", "=", low_day.id)]))
        q_account = self._account(self.q)
        response = self.url_open("/my/school/meals/topup", data={
            "csrf_token": token, "account_id": q_account.id, "amount": "50"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(q_account.topup_ids)

    def test_menu_day_reads_its_meals(self):
        day = self._day(5)
        self.assertIn("Pâtes sauce tomate", day.display_name, "the calendar shows the meals, not the date")
        day.action_close("Tempête")
        self.assertNotIn("Pâtes", day.display_name)

    # Adversarial review (2026-09-27)
    def test_order_state_and_price_move_with_the_buttons_only(self):
        self._fund(20)
        office = new_test_user(self.env, login="school_ml_office", groups="bf_school_core.group_school_manager")
        order = self._place().with_user(office)
        order.action_cancel()
        for vals in ({"state": "ordered"}, {"price": 0.5}, {"mode": "monthly"}, {"invoice_id": False}):
            with self.assertRaises(UserError):
                order.write(vals)
        self.assertEqual(self._account().balance, 20, "refunded once")

    def test_an_adult_who_only_receives_notices_does_not_spend(self):
        step = new_test_user(self.env, login="school_ml_step", groups="base.group_portal", email="step@essai.test")
        self.env["bf.school.guardian.link"].create({"student_id": self.a.id, "guardian_id": step.partner_id.id,
                                                    "relationship": "other", "receives_notices": True})
        self._fund(20)
        with self.assertRaises(UserError):
            self._place(partner=step)
        self.assertEqual(self._account().balance, 20)

    def test_topup_amount_must_be_a_number(self):
        account = self._account()
        for amount in (float("nan"), float("inf")):
            with self.assertRaises(UserError):
                account._school_topup(amount)

    def test_undone_payment_takes_the_credit_back(self):
        account = self._account()
        topup = account._school_topup(50)
        payment = self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=topup.invoice_id.ids).create({})._create_payments()
        self.assertEqual(account.balance, 50)
        payment.action_draft()
        self.assertEqual(topup.state, "pending")
        self.assertEqual(account.balance, 0, "the credit follows the payment")
        payment.action_post()
        self.assertEqual(account.balance, 0, "posting again does not reconcile by itself")

    def test_kitchen_list_is_tomorrows_ordered_meals(self):
        """"Ordered" and "Tomorrow" side by side were OR'd by the search view: the kitchen
        list opened on every order of every date (demo, 2026-10-02). Filters of one group
        are OR'd, a separator ANDs the groups."""
        from lxml import etree
        arch = etree.fromstring(self.env.ref("bf_school_meal.bf_school_meal_order_view_search").arch)
        groups, current = [], []
        for node in arch:
            if node.tag in ("separator", "group"):
                if current:
                    groups.append(current)
                    current = []
            elif node.tag == "filter":
                current.append(node.get("name"))
        if current:
            groups.append(current)
        group_of = {name: index for index, names in enumerate(groups) for name in names}
        self.assertNotEqual(group_of["ordered"], group_of["tomorrow"])
        self.assertNotEqual(group_of["tomorrow"], group_of["allergies"])
        self.assertEqual(group_of["today"], group_of["tomorrow"], "today or tomorrow, not both")
