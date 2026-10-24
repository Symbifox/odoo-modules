from dateutil.relativedelta import relativedelta
from freezegun import freeze_time

from odoo import Command

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import float_round

from .common import BillingFixture


@tagged("post_install", "-at_install")
class TestChildcareBilling(BillingFixture, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_billing()

    def _rl24_lines(self, invoice):
        return invoice.invoice_line_ids.filtered("school_rl24")

    # --- What is charged ---------------------------------------------------------------------------

    def test_occasional_period_and_short_notice(self):
        self._book()
        with freeze_time(self._at(self.monday, 8.0)):
            late = self._office_book(student=self.eli)
        self.assertTrue(late.late_notice)
        run = self._bill()
        alice = self._charges(self.alice)
        self.assertEqual([(c.kind, c.amount, c.eligible_amount, c.state) for c in alice],
                         [("slot", 12.0, 12.0, "billed")])
        self.assertEqual(sorted((c.kind, c.amount) for c in self._charges(self.eli)),
                         [("late_notice", 3.0), ("slot", 12.0)])
        invoice = self._invoices(self.mom.partner_id)
        self.assertEqual(len(invoice), 1, "one invoice per payer and month, every child on it")
        self.assertEqual((invoice.state, invoice.amount_total), ("posted", 27.0))
        self.assertEqual(invoice.invoice_line_ids.school_childcare_student_id, self.alice | self.eli)
        self.assertFalse(invoice.invoice_line_ids.tax_ids, "child care is exempt")
        self.assertFalse(invoice.invoice_user_id, "sent by the school, not by the person who billed")
        self.assertEqual(len(self._rl24_lines(invoice)), 3)
        self.assertFalse(self._invoices(self.dad.partner_id), "the adult who does not pay receives nothing")
        # In the payer's language, whoever billed.
        line = invoice.invoice_line_ids.filtered(lambda l: l.school_childcare_student_id == self.alice)
        self.assertEqual(line.name[:len("Alice Garde : Période occasionnelle")], "Alice Garde : Période occasionnelle")
        self.assertIn("Service de garde", invoice.invoice_origin)
        self.assertEqual((run.state, run.invoice_count, run.amount_billed), ("billed", 1, 27.0))
        self.assertEqual(run.month_label[-4:], str(self.month.year))
        self.assertFalse(run.message_ids.filtered(lambda m: m.message_type == "notification" and "créé" in (m.body or "")),
                         "no creation note: Odoo's would agree in gender with nothing")

    def test_a_day_rate_charges_one_day_whatever_the_periods(self):
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "day", "amount": 20.0})
        self._book(slot=self.before)
        self._book()
        self._bill()
        self.assertEqual([(c.kind, c.amount) for c in self._charges(self.alice)], [("day", 20.0)])

    def test_a_late_cancellation_stays_due(self):
        early, late = self._book(), self._book(student=self.eli)
        early._school_cancel_by(self.mom.partner_id)
        with freeze_time(self._at(self.monday, 14.0)):
            late._school_cancel_by(self.mom.partner_id)
        self.assertEqual((early.cancel_late, late.cancel_late), (False, True))
        self._bill()
        self.assertFalse(self._charges(self.alice), "cancelled in time: nothing due")
        self.assertEqual(self._charges(self.eli).mapped("amount"), [12.0], "place reserved, place paid")

    def test_a_closed_day_charges_nothing(self):
        booking = self._book(slot=self.full, day=self.pedagogical)
        self.env["bf.school.calendar.day"].search([("school_id", "=", self.school.id),
                                                   ("date", "=", self.pedagogical)]).kind = "closure"
        self.assertEqual(booking.state, "closed")
        self._bill()
        self.assertFalse(self._charges(self.alice))

    def test_child_not_booked_and_late_pick_up(self):
        self._book()
        sheet = self._sheet()
        with freeze_time(self._at(self.monday, 15.6)):
            self.env["bf.school.childcare.sheet.line"].with_user(self.educator).create(
                {"sheet_id": sheet.id, "student_id": self.dina.id})
        line = self._attend(self.alice, depart=18 + 25 / 60)
        self.assertEqual(line.late_minutes, 20)
        self._bill()
        late = self._charges(self.alice, kind="late_pickup")
        self.assertEqual((late.amount, late.eligible_amount, late.late_minutes), (15.0, 0.0, 20),
                         "a first block, then a block begun; a penalty is not child care")
        self.assertEqual([(c.kind, c.amount) for c in self._charges(self.dina)], [("walk_in", 18.0)])
        # Dina's family has nobody ticked « Pays »: the adult with parental authority who signs pays.
        self.assertEqual(self._invoices(self.other.partner_id).amount_total, 18.0)
        invoice = self._invoices(self.mom.partner_id)
        self.assertEqual(invoice.amount_total, 27.0)
        self.assertEqual(sum(self._rl24_lines(invoice).mapped("price_total")), 12.0)

    def test_late_pick_up_blocks(self):
        rate = self.r_late
        self.assertEqual([rate._school_late_amount(m) for m in (0, 1, 15, 16, 30, 31)],
                         [0.0, 10.0, 10.0, 15.0, 15.0, 20.0])
        rate.next_block_amount = 0.0
        self.assertEqual(rate._school_late_amount(16), 20.0, "no price for the next blocks: the first one's")

    def test_a_child_not_booked_without_its_own_rate_pays_the_occasional_one(self):
        self.r_walk.active = False
        sheet = self._sheet()
        with freeze_time(self._at(self.monday, 15.6)):
            self.env["bf.school.childcare.sheet.line"].with_user(self.educator).create(
                {"sheet_id": sheet.id, "student_id": self.dina.id})
        self._bill()
        self.assertEqual([(c.kind, c.amount) for c in self._charges(self.dina)], [("slot", 12.0)])

    def test_regular_registration_by_the_month_prorated(self):
        end = self.month + relativedelta(months=1, days=-1)
        days = sorted(self.env["bf.school.calendar.day"].search([
            ("school_id", "=", self.school.id), ("date", ">=", self.month), ("date", "<=", end),
            ("kind", "=", "class"), ("childcare_open", "=", True)]).mapped("date"))
        # Begun after the first class day of the month: the tier is prorated.
        start = days[1]
        registration = self._registration(lines={self.after: ["mon", "wed"]}, start=start)
        self.assertEqual(registration.periods_per_week, 2)
        self._bill()
        covered = [day for day in days if day >= start]
        self.assertLess(len(covered), len(days))
        charge = self._charges(self.alice, kind="regular", month=self.month)
        self.assertEqual(charge.amount, float_round(100.0 * len(covered) / len(days), 2))
        self.assertEqual(charge.eligible_amount, charge.amount)
        self.assertEqual(charge.registration_id, registration)

    def test_regular_days_without_class_at_their_rate_out_of_the_tier(self):
        self._registration(lines={self.after: ["mon"], self.full: ["mon", "tue", "wed", "thu", "fri"]},
                           start=self.monday)
        self._bill()
        regular = self._charges(self.alice, kind="regular", month=self.month)
        end = self.month + relativedelta(months=1, days=-1)
        days = self.env["bf.school.calendar.day"].search([
            ("school_id", "=", self.school.id), ("date", ">=", self.month), ("date", "<=", end),
            ("kind", "=", "class"), ("childcare_open", "=", True)]).mapped("date")
        self.assertEqual(regular.amount, float_round(100.0 * len([d for d in days if d >= self.monday]) / len(days), 2),
                         "one period a week: the day without class is not a period of the tier")
        full = self._charges(self.alice, kind="full_day")
        self.assertIn(self.pedagogical, full.mapped("date"))
        self.assertEqual(full.filtered(lambda c: c.date == self.pedagogical).amount, 47.0)

    def test_regular_registration_on_the_contract_charges_nothing_here(self):
        self.school.childcare_billing = "contract"
        self._registration(lines={self.after: ["mon", "wed", "fri"]}, start=self.monday)
        self._book(slot=self.full, day=self.pedagogical)
        self._bill()
        self.assertEqual(self._charges(self.alice).mapped("kind"), ["full_day"])

    def test_day_without_class_its_cap_and_the_activity_fee(self):
        self.env["bf.school.childcare.activity"].with_user(self.office).create(
            {"name": "Cabane à sucre", "slot_id": self.full.id, "date": self.pedagogical, "fee": 25.0})
        self._book(slot=self.full, day=self.pedagogical)
        self._bill()
        full, fee = self._charges(self.alice, kind="full_day"), self._charges(self.alice, kind="activity")
        self.assertEqual((full.amount, full.eligible_amount), (47.0, 22.0))
        self.assertEqual((fee.amount, fee.eligible_amount), (25.0, 0.0), "an outing is not child care")
        invoice = self._invoices(self.mom.partner_id)
        self.assertEqual(invoice.amount_total, 72.0)
        self.assertEqual(sum(self._rl24_lines(invoice).mapped("price_total")), 22.0)
        self.assertEqual(len(invoice.invoice_line_ids), 3, "the day in its child-care part and the rest, the fee")

    def test_a_missing_price_is_told(self):
        self.r_full.active = False
        self._book(slot=self.full, day=self.pedagogical)
        self._bill()
        self.assertFalse(self._charges(self.alice))
        run = self._run(self.pedagogical.replace(day=1))
        self.assertIn("Journée pédagogique", " ".join(run.message_ids.mapped("body")))

    # --- Who pays -------------------------------------------------------------------------------

    def test_shared_custody_follows_the_contract(self):
        contract = self.env["bf.school.contract"].create({
            "student_id": self.alice.id, "school_id": self.school.id, "year_id": self.year.id,
            "date_start": self.year.date_start, "date_end": self.year.date_end, "tuition": 1000.0})
        contract.share_ids.sudo().unlink()
        Share = self.env["bf.school.billing.share"].sudo()
        Share.create({"contract_id": contract.id, "partner_id": self.mom.partner_id.id, "kind": "family",
                      "percent": 60.0})
        Share.create({"contract_id": contract.id, "partner_id": self.dad.partner_id.id, "kind": "family",
                      "percent": 40.0})
        contract.sudo().state = "signed"
        self._book()
        self._book(slot=self.full, day=self.pedagogical)
        self._bill()
        mom, dad = self._invoices(self.mom.partner_id), self._invoices(self.dad.partner_id)
        self.assertEqual((sum(mom.mapped("amount_total")), sum(dad.mapped("amount_total"))), (35.4, 23.6))
        self.assertAlmostEqual(sum(mom.invoice_line_ids.filtered("school_rl24").mapped("price_total")), 20.4)
        self.assertAlmostEqual(sum(dad.invoice_line_ids.filtered("school_rl24").mapped("price_total")), 13.6)

    def test_nobody_pays_the_school_is_told(self):
        self.bruno.student_guardian_link_ids.write({"is_payer": False, "can_sign": False})
        self._book(student=self.bruno, slot=self.after_k)
        run = self._bill()
        self.assertEqual(self._charges(self.bruno).state, "open", "billed once someone pays")
        self.assertIn("Bruno Garde", " ".join(run.message_ids.mapped("body")))
        self.assertEqual(run.activity_ids.user_id, self.office, "told to the Administration person who billed")

    def test_a_charge_left_open_is_billed_under_its_own_month(self):
        self.bruno.student_guardian_link_ids.write({"is_payer": False, "can_sign": False})
        self._book(student=self.bruno, slot=self.after_k)
        self._bill()
        self.bruno.student_guardian_link_ids.filtered(lambda l: l.guardian_id == self.dad.partner_id).is_payer = True
        later = self.months[-1] + relativedelta(months=1)
        with freeze_time(self._at(later + relativedelta(months=1, day=2), 9.0)):
            self._run(later).action_bill()
        invoice = self._invoices(self.dad.partner_id)
        self.assertEqual(invoice.amount_total, 12.0)
        self.assertEqual(invoice.school_childcare_run_id.month, self.month,
                         "billed under the billing of the month served: the Relevé 24 counts its year")

    # --- Tickets ------------------------------------------------------------------------------------

    def test_tickets_pay_the_occasional_periods_first(self):
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "ticket_book", "amount": 30.0, "tickets": 1})
        Book = self.env["bf.school.childcare.ticket_book"]
        for adult in (self.grandma, self.other):
            with self.assertRaises(AccessError, msg=adult.login):
                Book._school_buy(adult.partner_id, self.alice)
        book = Book._school_buy(self.mom.partner_id, self.alice)
        self.assertEqual((book.state, book.tickets, book.invoice_id.amount_total), ("to_pay", 1, 30.0))
        self.assertEqual(book.invoice_id.invoice_line_ids.school_childcare_student_id, self.alice)
        self._pay(book.invoice_id)
        self.assertEqual((book.state, book.remaining), ("paid", 1))
        self._book(slot=self.before)
        self._book()
        self._bill()
        charges = self._charges(self.alice)
        self.assertEqual(sorted(charges.mapped("state")), ["billed", "ticket"])
        self.assertEqual((book.used, book.remaining), (1, 0))
        self.assertEqual(self._invoices(self.mom.partner_id).amount_total, 12.0)
        # The payment undone: the book's invoice is due again; what it paid stays paid by it.
        book.invoice_id.line_ids.remove_move_reconcile()
        self.assertEqual((book.state, book.remaining), ("to_pay", 0))
        self.assertEqual(sorted(charges.mapped("state")), ["billed", "ticket"])

    def test_a_second_click_opens_the_book_to_pay_and_its_pdf_is_made(self):
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "ticket_book", "amount": 30.0, "tickets": 2})
        Book = self.env["bf.school.childcare.ticket_book"]
        book = Book._school_buy(self.mom.partner_id, self.alice)
        self.assertEqual(Book._school_buy(self.mom.partner_id, self.alice), book, "no second invoice")
        self.assertNotEqual(Book._school_buy(self.dad.partner_id, self.alice), book, "each adult their own")
        Book._cron_invoice_pdf()
        self.assertTrue(book.invoice_id.invoice_pdf_report_id, "the official PDF, not a « PROFORMA »")
        self.assertEqual(book.invoice_id.message_partner_ids, self.mom.partner_id, "only the payer follows it")

    def test_a_ticket_spent_on_a_day_closed_comes_back(self):
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "ticket_book", "amount": 30.0, "tickets": 1})
        book = self.env["bf.school.childcare.ticket_book"]._school_buy(self.mom.partner_id, self.alice)
        self._pay(book.invoice_id)
        self._book()
        with freeze_time(self._at(self.bill_day, 9.0)):
            run = self._run()
            run.action_compute()
            self.assertEqual(self._charges(self.alice).state, "ticket")
            self.env["bf.school.calendar.day"].search([("school_id", "=", self.school.id),
                                                       ("date", "=", self.monday)]).sudo().kind = "closure"
            run.action_compute()
        self.assertFalse(self._charges(self.alice))
        self.assertEqual(book.remaining, 1)

    def test_a_credited_book_pays_nothing_more(self):
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "ticket_book", "amount": 30.0, "tickets": 5})
        book = self.env["bf.school.childcare.ticket_book"]._school_buy(self.mom.partner_id, self.alice)
        self._pay(book.invoice_id)
        reversal = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=book.invoice_id.ids).create(
            {"journal_id": book.invoice_id.journal_id.id, "reason": "Remboursé"})
        self.env["account.move"].browse(reversal.refund_moves()["res_id"]).action_post()
        self._book()
        self._bill()
        self.assertEqual(self._charges(self.alice).state, "billed")
        self.assertEqual(book.remaining, 0)

    def test_a_book_not_paid_is_cancelled_with_its_invoice(self):
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "ticket_book", "amount": 30.0, "tickets": 5})
        book = self.env["bf.school.childcare.ticket_book"]._school_buy(self.mom.partner_id, self.alice)
        with self.assertRaises(AccessError):
            book.with_user(self.manager).action_cancel()
        book.with_user(self.office).action_cancel()
        self.assertEqual((book.state, book.invoice_id.state), ("cancelled", "cancel"))

    def test_a_book_paid_is_not_cancelled(self):
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "ticket_book", "amount": 30.0, "tickets": 5})
        book = self.env["bf.school.childcare.ticket_book"]._school_buy(self.mom.partner_id, self.alice)
        self._pay(book.invoice_id, 10.0)
        with self.assertRaisesRegex(UserError, "not paid at all"):
            book.with_user(self.office).action_cancel()
        self.assertEqual((book.state, book.invoice_id.state), ("to_pay", "posted"))

    # --- The ledger -----------------------------------------------------------------------------

    def test_open_charges_follow_the_rates_billed_ones_never_change(self):
        self._book()
        with freeze_time(self._at(self.bill_day, 9.0)):
            run = self._run()
            run.action_compute()
            charge = self._charges(self.alice)
            self.assertEqual((charge.amount, charge.state), (12.0, "open"))
            self.r_slot.amount = 14.0
            run.action_compute()
            self.assertEqual(charge.amount, 14.0)
        self._bill()
        self.r_slot.amount = 99.0
        with freeze_time(self._at(self.bill_day, 9.0)):
            run.action_compute()
        self.assertEqual((charge.amount, charge.state), (14.0, "billed"))
        self._bill()
        self.assertEqual(len(self._invoices(self.mom.partner_id)), 1, "billed again, nothing new")

    def test_a_month_is_billed_once_over(self):
        self._book()
        with freeze_time(self._at(self.monday, 19.0)):
            run = self._run()
            with self.assertRaisesRegex(UserError, "has not ended"):
                run.action_bill()
            with self.assertRaisesRegex(UserError, "has not ended"):
                run.action_compute()

    def test_waived_charges_are_not_billed(self):
        self._book()
        with freeze_time(self._at(self.bill_day, 9.0)):
            self._run().action_compute()
        charge = self._charges(self.alice)
        with self.assertRaises(AccessError):
            charge.with_user(self.manager).action_waive("Erreur")
        with self.assertRaisesRegex(UserError, "why"):
            charge.with_user(self.office).action_waive(" ")
        wizard = self.env["bf.school.childcare.charge.waive"].with_user(self.office).with_context(
            active_ids=charge.ids).create({"reason": "Erreur de l'école"})
        wizard.action_waive()
        self.assertEqual((charge.state, charge.waived_by_id), ("waived", self.office))
        self._bill()
        self.assertEqual(charge.state, "waived")
        self.assertFalse(self._invoices(self.mom.partner_id))
        with self.assertRaisesRegex(UserError, "still to bill"):
            charge.with_user(self.office).action_waive("Encore")

    def test_nobody_writes_the_ledger_by_hand(self):
        self._book()
        run = self._bill()
        charge = self._charges(self.alice)
        office = self.office
        Charge = self.env["bf.school.childcare.charge"].with_user(office)
        with self.assertRaisesRegex(UserError, "come from the bookings"):
            Charge.create({"student_id": self.alice.id, "school_id": self.school.id, "year_id": self.year.id,
                           "date": self.monday, "kind": "slot", "amount": 1.0, "key": "forged"})
        with self.assertRaisesRegex(UserError, "not changed by hand"):
            charge.with_user(office).write({"amount": 1.0})
        with self.assertRaisesRegex(UserError, "not deleted"):
            charge.with_user(office).unlink()
        with self.assertRaisesRegex(UserError, "keeps its school and its month"):
            run.with_user(office).write({"month": self.month + relativedelta(months=2)})
        with self.assertRaisesRegex(UserError, "starts to bill"):
            self.env["bf.school.childcare.billing.run"].with_user(office).create(
                {"school_id": self.school.id, "month": self.month + relativedelta(months=3), "state": "billed"})
        with self.assertRaisesRegex(UserError, "bought on the family portal"):
            self.env["bf.school.childcare.ticket_book"].with_user(office).create({})
        for user in (self.educator, self.teacher):
            with self.assertRaises(AccessError, msg=user.login):
                Charge.with_user(user).search([])

    def test_the_accounting_does_not_change_a_child_care_invoice(self):
        self._book()
        self._bill()
        invoice = self._invoices(self.mom.partner_id)
        accountant = new_test_user(self.env, login="cc_accountant", groups="account.group_account_manager")
        line = invoice.invoice_line_ids[:1].with_user(accountant)
        with self.assertRaisesRegex(UserError, "billing sets this"):
            line.write({"school_rl24": False})
        with self.assertRaisesRegex(UserError, "billing sets this"):
            line.write({"school_childcare_student_id": self.eli.id})
        with self.assertRaisesRegex(UserError, "not reset to draft"):
            invoice.with_user(accountant).button_draft()
        with self.assertRaisesRegex(UserError, "billing sets this"):
            invoice.with_user(accountant).write({"school_childcare_run_id": False})
        with self.assertRaisesRegex(UserError, "billing sets this"):
            self.env["account.move.line"].with_user(accountant).create({
                "move_id": invoice.id, "name": "Forgée", "price_unit": 5.0, "school_rl24": True})
        with self.assertRaisesRegex(UserError, "billing sets this"):
            self.env["account.move"].with_user(accountant).create({
                "move_type": "out_invoice", "partner_id": self.mom.partner_id.id,
                "school_childcare_run_id": invoice.school_childcare_run_id.id})
        self.assertEqual(invoice.message_partner_ids, self.mom.partner_id, "only the payer follows it")
        # A credit note copies the lines it reverses: it lowers the Relevé 24, as it should.
        reversal = self.env["account.move.reversal"].with_user(accountant).with_context(
            active_model="account.move", active_ids=invoice.ids).create(
            {"journal_id": invoice.journal_id.id, "reason": "Erreur"})
        refund = self.env["account.move"].browse(reversal.refund_moves()["res_id"])
        self.assertEqual(refund.invoice_line_ids.school_childcare_student_id, self.alice)

    def test_rates_and_tiers_are_checked(self):
        Rate, Tier = self.env["bf.school.childcare.rate"].with_user(self.office), \
            self.env["bf.school.childcare.tier"].with_user(self.office)
        base = {"school_id": self.school.id, "year_id": self.year.id}
        with self.assertRaisesRegex(ValidationError, "already exists"):
            Rate.create(dict(base, kind="slot", amount=5.0))
        Rate.create(dict(base, kind="slot", slot_id=self.before.id, amount=5.0))
        with self.assertRaisesRegex(ValidationError, "whole school"):
            Rate.create(dict(base, kind="late_notice", slot_id=self.before.id, amount=5.0))
        with self.assertRaisesRegex(ValidationError, "one ticket"):
            Rate.create(dict(base, kind="ticket_book", amount=5.0))
        with self.assertRaisesRegex(ValidationError, "same number of periods"):
            Tier.create(dict(base, min_periods=2, max_periods=4, amount=10.0))
        with self.assertRaises(AccessError):
            Rate.with_user(self.manager).create(dict(base, kind="day", amount=5.0))
        self.assertFalse(self.r_late.rl24, "late pick-up is not child care by default")
        self.assertTrue(self.r_slot.rl24)
        self.assertEqual(Rate._school_rate(self.school, self.year, "slot", self.before).amount, 5.0,
                         "a period's own rate wins")
        self.assertEqual(Rate._school_rate(self.school, self.year, "slot", self.after).amount, 12.0)

    # --- Monthly cron, sending -----------------------------------------------------------------------

    def test_the_first_of_the_month_bills_and_sends(self):
        self._book()
        Run = self.env["bf.school.childcare.billing.run"]
        with freeze_time(self._at(self.month + relativedelta(months=1), 6.5)):
            Run._cron_bill_last_month()
        invoice = self._invoices(self.mom.partner_id)
        self.assertEqual(invoice.amount_total, 12.0)
        Run._cron_send_invoices()
        self.assertTrue(invoice.school_billing_sent)
        self.assertTrue(invoice.message_ids.filtered(lambda m: self.mom.partner_id in m.partner_ids
                                                     and m.attachment_ids))

    def test_the_callers_defaults_stay_out(self):
        """🔴 The clicker's `default_*` context and own ir.default."""
        Default = self.env["ir.default"]
        Default.set("bf.school.childcare.charge", "state", "waived", user_id=self.office.id)
        Default.set("account.move.line", "discount", 100.0, user_id=self.office.id)
        self._book()
        with freeze_time(self._at(self.bill_day, 9.0)):
            Run = self.env["bf.school.childcare.billing.run"].with_user(self.office).with_context(
                default_state="billed", default_billed_on="2020-01-01 00:00:00")
            # In order: the month of the booking is billed by its own billing, under the forged context
            # (which `sudo()` already cleans in Odoo 18; `_school_invoice` cleans it again for a caller in sudo).
            for month in self.months:
                run = Run.search([("school_id", "=", self.school.id), ("month", "=", month)]) or Run.create(
                    {"school_id": self.school.id, "month": month})
                self.assertEqual((run.state, run.billed_on), ("draft", False))
                run.with_context(default_state="waived", default_discount=100.0, default_narration="Forgé",
                                 default_ref="Forgé", default_ticket_book_id=1).action_bill()
        charge = self._charges(self.alice)
        self.assertEqual((charge.state, charge.ticket_book_id.id), ("billed", False))
        invoice = self._invoices(self.mom.partner_id)
        self.assertEqual(invoice.amount_total, 12.0)
        self.assertFalse(invoice.invoice_line_ids.filtered("discount"))
        self.assertNotEqual(invoice.ref, "Forgé")

    def test_the_activity_fee_is_the_administrations(self):
        activity = self.env["bf.school.childcare.activity"].with_user(self.manager).create(
            {"name": "Cinéma", "slot_id": self.full.id, "date": self.pedagogical})
        with self.assertRaisesRegex(UserError, "Administration sets the fee"):
            activity.with_user(self.manager).write({"fee": 80.0})
        with self.assertRaisesRegex(UserError, "Administration sets the fee"):
            activity.with_user(self.manager).write({"fee_rl24": True})
        with self.assertRaisesRegex(UserError, "Administration sets the fee"):
            self.env["bf.school.childcare.activity"].with_user(self.manager).create(
                {"name": "Zoo", "slot_id": self.full.id, "date": self.pedagogical, "fee": 10.0})
        activity.with_user(self.manager).write({"fee": 0.0, "name": "Cinéma du quartier"})
        activity.with_user(self.office).write({"fee": 80.0})
        self.assertEqual(activity.fee, 80.0)

    def test_a_payer_without_email_is_told_to_the_school(self):
        self.mom.partner_id.email = False
        self._book()
        run = self._bill()
        self.env["bf.school.childcare.billing.run"]._cron_send_invoices()
        invoice = self._invoices(self.mom.partner_id)
        self.assertTrue(invoice.school_billing_sent)
        self.assertIn("no email address", " ".join(run.message_ids.mapped("body")))
        self.assertEqual(run.activity_ids.user_id, self.office)

    # --- Relevé 24 -----------------------------------------------------------------------------------

    def test_rl24_counts_the_child_care_paid(self):
        self._book(slot=self.full, day=self.pedagogical)
        self._bill()
        invoice = self._invoices(self.mom.partner_id)
        self._pay(invoice, 23.5)
        RL24, key = self.env["bf.school.rl24"], (self.mom.partner_id.id, self.alice.id)
        paid = RL24._school_paid(self.school.company_id, self.pedagogical.year)
        self.assertEqual(paid[key], 11.0, "half paid: half of 22")
        self.assertFalse(RL24._school_paid(self.school.company_id, self.pedagogical.year + 1).get(key),
                         "counted in the year of the services")
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "ticket_book", "amount": 30.0, "tickets": 3,
                                                     "rl24_cap": 8.0})
        book = self.env["bf.school.childcare.ticket_book"]._school_buy(self.mom.partner_id, self.alice)
        self._pay(book.invoice_id)
        paid_on = book.invoice_id.line_ids.matched_credit_ids.max_date
        self.assertEqual(paid_on.year, fields.Date.context_today(book).year)
        expected = 24.0 + (11.0 if paid_on.year == self.pedagogical.year else 0.0)
        self.assertEqual(RL24._school_paid(self.school.company_id, paid_on.year)[key], expected,
                         "a book counts in the year it is paid, 8 a ticket")
        self.assertFalse(RL24._school_paid(self.school.company_id, paid_on.year + 1).get(key),
                         "not in the year after: nothing was paid then")

    def test_a_credit_note_only_lowers_the_rl24(self):
        self._book(slot=self.full, day=self.pedagogical)
        self._bill()
        invoice = self._invoices(self.mom.partner_id)
        self._pay(invoice)
        RL24, key, year = self.env["bf.school.rl24"], (self.mom.partner_id.id, self.alice.id), self.pedagogical.year
        accountant = new_test_user(self.env, login="cc_accountant2", groups="account.group_account_manager")
        # 🔴 A credit note of 1 $ made of a negative child-care line and a positive one.
        reversal = self.env["account.move.reversal"].with_user(accountant).with_context(
            active_model="account.move", active_ids=invoice.ids).create(
            {"journal_id": invoice.journal_id.id, "reason": "Erreur"})
        refund = self.env["account.move"].with_user(accountant).browse(reversal.refund_moves()["res_id"])
        refund.write({"invoice_line_ids": [Command.clear()] + [
            Command.create({"name": "Forgée", "price_unit": -300.0, "tax_ids": [Command.clear()], "school_rl24": True,
                            "school_childcare_student_id": self.alice.id}),
            Command.create({"name": "Équilibre", "price_unit": 301.0, "tax_ids": [Command.clear()]})]})
        refund.action_post()
        # And an invoice posing as a reversal.
        fake = self.env["account.move"].with_user(accountant).create({
            "move_type": "out_invoice", "partner_id": self.mom.partner_id.id, "reversed_entry_id": invoice.id,
            "invoice_line_ids": [Command.create({"name": "Forgée", "price_unit": 100.0, "tax_ids": [Command.clear()],
                                                 "school_rl24": True, "school_childcare_student_id": self.alice.id})]})
        fake.action_post()
        self.assertEqual(RL24._school_paid(self.school.company_id, year)[key], 22.0)
        # A true credit note of the whole invoice brings it to nothing.
        reversal = self.env["account.move.reversal"].with_user(accountant).with_context(
            active_model="account.move", active_ids=invoice.ids).create(
            {"journal_id": invoice.journal_id.id, "reason": "Annulée"})
        self.env["account.move"].browse(reversal.refund_moves()["res_id"]).action_post()
        self.assertFalse(RL24._school_paid(self.school.company_id, year).get(key))

    # --- Money, credit notes and notices ----------------------------------------------------------------

    def _accountant(self, login):
        return new_test_user(self.env, login=login, groups="account.group_account_manager")

    def _credit(self, invoice, user, lines=None):
        reversal = self.env["account.move.reversal"].with_user(user).with_context(
            active_model="account.move", active_ids=invoice.ids).create(
            {"journal_id": invoice.journal_id.id, "reason": "Crédit"})
        refund = self.env["account.move"].with_user(user).browse(reversal.refund_moves()["res_id"])
        if lines is not None:
            refund.write({"invoice_line_ids": [Command.clear()] + lines})
        refund.action_post()
        return refund

    def test_rl24_never_more_than_the_money_received(self):
        """🔴 A credit note of a line not ticked (a discount) left the child care counted in full."""
        self._book()
        self._book(day=self.tuesday)
        self._bill()
        invoice = self._invoices(self.mom.partner_id)
        self.assertEqual(invoice.amount_total, 24.0)
        self._pay(invoice, 12.0)
        self._credit(invoice, self._accountant("cc_acc_d1"), [Command.create(
            {"name": "Geste commercial", "price_unit": 12.0, "tax_ids": [Command.clear()]})])
        paid = self.env["bf.school.rl24"]._school_paid(self.school.company_id, self.monday.year)
        self.assertEqual(paid[(self.mom.partner_id.id, self.alice.id)], 12.0, "12 $ received: 12 $ at most")

    def test_a_credit_note_by_hand_is_not_money(self):
        """🔴 A credit note made by hand, credited to no invoice, settled the invoice as if paid."""
        self._book()
        self._bill()
        invoice = self._invoices(self.mom.partner_id)
        accountant = self._accountant("cc_acc_d2")
        refund = self.env["account.move"].with_user(accountant).create({
            "move_type": "out_refund", "partner_id": self.mom.partner_id.id,
            "invoice_line_ids": [Command.create({"name": "Annulation", "price_unit": 12.0,
                                                 "tax_ids": [Command.clear()]})]})
        refund.action_post()
        (invoice | refund).line_ids.filtered(
            lambda l: l.account_id.account_type == "asset_receivable" and not l.reconciled).reconcile()
        self.assertFalse(invoice.amount_residual, "settled by the credit note")
        paid = self.env["bf.school.rl24"]._school_paid(self.school.company_id, self.monday.year)
        self.assertFalse(paid.get((self.mom.partner_id.id, self.alice.id)), "nothing was received")

    def test_a_credited_book_not_paid_does_not_block_the_family(self):
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "ticket_book", "amount": 30.0, "tickets": 3})
        Book = self.env["bf.school.childcare.ticket_book"]
        book = Book._school_buy(self.mom.partner_id, self.alice)
        self._credit(book.invoice_id, self._accountant("cc_acc_d3"))
        again = Book._school_buy(self.mom.partner_id, self.alice)
        self.assertNotEqual(again, book, "a new book, not the credited one")
        self.assertEqual(book.state, "cancelled")

    def test_a_book_credited_in_part_keeps_its_share_of_tickets(self):
        self.env["bf.school.childcare.rate"].create({"school_id": self.school.id, "year_id": self.year.id,
                                                     "kind": "ticket_book", "amount": 40.0, "tickets": 4})
        book = self.env["bf.school.childcare.ticket_book"]._school_buy(self.mom.partner_id, self.alice)
        self._pay(book.invoice_id)
        self._credit(book.invoice_id, self._accountant("cc_acc_d4"), [Command.create(
            {"name": "Remboursement partiel", "price_unit": 20.0, "tax_ids": [Command.clear()]})])
        book.invalidate_recordset(["remaining", "usable"])
        self.assertEqual(book.remaining, 2, "half the price credited, half the tickets")

    def test_no_payer_told_once_and_by_the_cron_to_the_administration(self):
        self.bruno.student_guardian_link_ids.write({"is_payer": False, "can_sign": False})
        self._book(student=self.bruno, slot=self.after_k)
        Run = self.env["bf.school.childcare.billing.run"]
        with freeze_time(self._at(self.month + relativedelta(months=1), 6.5)):
            Run._cron_bill_last_month()
            Run._cron_bill_last_month()
        run = Run.search([("school_id", "=", self.school.id), ("month", "=", self.month)])
        activity = run.activity_ids
        self.assertEqual(len(activity), 1, "one activity, not one per billing")
        self.assertTrue(activity.user_id.has_group("bf_school_core.group_school_manager"),
                        "the cron bills as the system: the Administration is told, not the child-care manager")

