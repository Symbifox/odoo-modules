from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.bf_school_billing.tests.test_school_billing import BillingCase


@tagged("post_install", "-at_install")
class Rl24Case(BillingCase):
    """The kindergarten child: 4 000 $ of kindergarten less 15 % (third child) and 1 200 $ of
    child care are child care, 300 $ of material is not. 4 900 $, half each: 2 450 $ a parent,
    of which 2 300 $ of child care, in ten instalments of 245 $ from September 1."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.f_k5.amount = 4000.0
        cls.f_tuition.level_ids = cls.p3 | cls.p5
        cls.y1, cls.y2 = cls.start.year, cls.start.year + 1
        cls.Rl24 = cls.env["bf.school.rl24"]
        cls.company = cls.env.company

    def _billed(self):
        with freeze_time(date(self.y1, 8, 15)):
            contract = self._signed(self._prepare()[self.youngest])
            contract.action_school_bill()
        self.assertEqual(contract.total_price, 4900.0)
        return contract

    def _invoice(self, contract, partner):
        return contract.share_ids.filtered(lambda s: s.partner_id == partner).invoice_id

    def _pay_on(self, day, move, amount=None):
        with freeze_time(day):
            self._pay(move, amount)


@tagged("post_install", "-at_install")
class TestRl24Amounts(Rl24Case):

    def test_counted_in_the_year_of_the_services(self):
        contract = self._billed()
        mom = self._invoice(contract, self.mom)
        self._pay_on(date(self.y1, 12, 1), mom, 980.0)         # September to December
        self._pay_on(date(self.y2, 3, 1), mom)                 # the rest
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1), {(self.mom.id, self.youngest.id): 920.0})
        self.assertEqual(self.Rl24._school_paid(self.company, self.y2), {(self.mom.id, self.youngest.id): 1380.0})

    def test_paid_in_advance_counts_for_the_services_year(self):
        contract = self._billed()
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, self.dad))   # the whole year
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1)[(self.dad.id, self.youngest.id)], 920.0)
        self.assertEqual(self.Rl24._school_paid(self.company, self.y2)[(self.dad.id, self.youngest.id)], 1380.0)

    def test_no_slip_for_the_bursary_fund_and_for_unpaid(self):
        with freeze_time(date(self.y1, 8, 15)):
            contract = self._signed(self._prepare()[self.youngest])
            contract.share_ids = [(0, 0, {"partner_id": self.fund.id, "kind": "bursary", "amount": 900.0})]
            contract.action_school_bill()
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, self.fund))
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1), {},
                         "the fund is a company, and the parents paid nothing")

    def test_meals_and_other_invoices_never_count(self):
        self._billed()
        with freeze_time(date(self.y1, 10, 1)):
            meal = self.env["account.move"].create({
                "move_type": "out_invoice", "partner_id": self.mom.id,
                "invoice_line_ids": [(0, 0, {"name": "Repas", "quantity": 1, "price_unit": 80.0,
                                             "school_rl24": True})]})
            meal.action_post()
            self._pay(meal)
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1), {})

    def test_paid_back_after_a_termination_comes_off(self):
        contract = self._billed()
        mom = self._invoice(contract, self.mom)
        self._pay_on(date(self.y1, 9, 2), mom)                 # mom paid her whole share
        with freeze_time(date(self.y1, 10, 15)):
            contract.termination_date = date(self.y1, 10, 15)
            contract.action_terminate()
            refund = self.env["account.move"].search([
                ("school_contract_id", "=", contract.id), ("partner_id", "=", self.mom.id),
                ("move_type", "=", "out_refund")])
            self._pay(refund)                                  # the school pays her back
        # She paid 2 450 $, got 1 715 $ back: 735 $ kept, of which 490 $ for September and
        # October, the months provided, and 245 $ of penalty, which is not child care.
        self.assertEqual(contract.amount_paid, 735.0)
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1), {(self.mom.id, self.youngest.id): 460.0})
        self.assertEqual(self.Rl24._school_paid(self.company, self.y2), {},
                         "nothing for the services that never happened")


    def _credit(self, invoice, amount, care):
        refund = self.env["account.move"].create({
            "move_type": "out_refund", "partner_id": invoice.partner_id.id, "reversed_entry_id": invoice.id,
            "invoice_line_ids": [(0, 0, {"name": "Correction", "quantity": 1, "price_unit": amount,
                                         "tax_ids": [(5, 0, 0)], "school_rl24": care})]})
        refund.action_post()
        (refund.line_ids | invoice.line_ids).filtered(
            lambda l: l.display_type == "payment_term" and not l.reconciled).reconcile()
        return refund

    def test_a_credit_note_of_the_child_care_leaves_no_child_care(self):
        # 🔴 The 2 300 $ of child care credited, mom pays the 150 $ of material: no child care paid.
        contract = self._billed()
        mom = self._invoice(contract, self.mom)
        with freeze_time(date(self.y1, 9, 2)):
            self._credit(mom, 2300.0, True)
        self.assertEqual(mom.amount_residual, 150.0)
        self._pay_on(date(self.y1, 12, 1), mom)
        for year in (self.y1, self.y2):
            self.assertEqual(self.Rl24._school_paid(self.company, year).get((self.mom.id, self.youngest.id), 0.0),
                             0.0, year)

    def test_a_credit_note_never_raises_the_child_care(self):
        contract = self._billed()
        mom = self._invoice(contract, self.mom)
        with freeze_time(date(self.y1, 9, 2)):
            self._credit(mom, 150.0, False)
        self._pay_on(date(self.y1, 12, 1), mom)
        counted = sum(self.Rl24._school_paid(self.company, year).get((self.mom.id, self.youngest.id), 0.0)
                      for year in (self.y1, self.y2))
        # Only lowered, never raised: at most the invoice's share of what was paid.
        self.assertLessEqual(counted, 2300.0 * 2300.0 / 2450.0 + 0.02)
        self.assertGreater(counted, 2100.0)

    def test_an_invoice_posing_as_a_credit_note_is_not_the_contract_s_money(self):
        contract = self._billed()
        mom = self._invoice(contract, self.mom)
        with freeze_time(date(self.y1, 10, 1)):
            meal = self.env["account.move"].create({
                "move_type": "out_invoice", "partner_id": self.mom.id, "reversed_entry_id": mom.id,
                "invoice_line_ids": [(0, 0, {"name": "Repas", "quantity": 1, "price_unit": 1000.0,
                                             "tax_ids": [(5, 0, 0)]})]})
            meal.action_post()
            self._pay(meal)
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1), {})
        self.assertEqual(contract.share_ids.filtered(lambda s: s.partner_id == self.mom)._school_paid(), 0.0)

    def test_the_admission_fee_is_not_child_care(self):
        with freeze_time(date(self.y1, 8, 15)):
            contract = self._prepare()[self.youngest]
            contract.admission_fee = 200.0
            self._signed(contract).action_school_bill()
        # Mom owes 100 $ of admission now, then 245 $ a month: she pays both, to December.
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, self.mom), 1080.0)
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1), {(self.mom.id, self.youngest.id): 920.0})

    def test_no_slip_for_a_company_paying_a_family_share(self):
        employer = self.env["res.partner"].create({"name": "Employeur inc.", "is_company": True})
        with freeze_time(date(self.y1, 8, 15)):
            contract = self._signed(self._prepare()[self.youngest])
            contract.share_ids.filtered(lambda s: s.partner_id == self.dad).partner_id = employer
            contract.action_school_bill()
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, employer))
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1), {})

    def test_a_credit_note_paid_back_comes_off_its_year(self):
        contract = self._billed()
        mom = self._invoice(contract, self.mom)
        self._pay_on(date(self.y1, 12, 1), mom)                 # the whole year in advance
        with freeze_time(date(self.y2, 1, 10)):
            # The accounting credits June, and pays it back.
            refund = self.env["account.move"].create({
                "move_type": "out_refund", "partner_id": self.mom.id, "reversed_entry_id": mom.id,
                "school_contract_id": contract.id, "invoice_line_ids": [(0, 0, {
                    "name": "Juin", "quantity": 1, "price_unit": 245.0, "tax_ids": [(5, 0, 0)]})]})
            refund.action_post()
            self._pay(refund)
        paid = self.Rl24._school_paid(self.company, self.y2)[(self.mom.id, self.youngest.id)]
        self.assertEqual(paid, 1150.0, "January to May: 1 225 $ at the child-care share")
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1)[(self.mom.id, self.youngest.id)], 920.0)

    def test_terminated_before_the_services_no_statement(self):
        contract = self._billed()
        self._pay_on(date(self.y1, 8, 16), self._invoice(contract, self.mom))
        with freeze_time(date(self.y1, 8, 20)):
            contract.termination_date = date(self.y1, 8, 20)
            contract.action_terminate()
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1), {},
                         "the penalty kept is not child care: the child never came")

    def test_a_bursary_fund_entered_as_a_person_gets_nothing(self):
        person_fund = self.env["res.partner"].create({"name": "Fonds créé comme une personne"})
        with freeze_time(date(self.y1, 8, 15)):
            contract = self._signed(self._prepare()[self.youngest])
            contract.share_ids = [(0, 0, {"partner_id": person_fund.id, "kind": "bursary", "amount": 900.0})]
            contract.action_school_bill()
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, person_fund))
        self.assertEqual(self.Rl24._school_paid(self.company, self.y1), {})


@tagged("post_install", "-at_install")
class TestRl24Statements(Rl24Case):

    def test_compute_issue_and_flag_a_change(self):
        contract = self._billed()
        mom = self._invoice(contract, self.mom)
        self._pay_on(date(self.y1, 12, 1), mom, 735.0)          # September to November
        with freeze_time(date(self.y2, 1, 20)):
            statements = self.Rl24.with_user(self.office)._school_compute(self.company, self.y1)
            self.assertEqual(len(statements), 1)
            self.assertEqual((statements.partner_id, statements.amount), (self.mom, 690.0))
            statements.action_issue()
            self.assertEqual(statements.state, "issued")
            self.assertFalse(statements.is_outdated)
            # The cheque for December arrives in January: it pays a service of the year.
            self._pay(mom, 245.0)
            self.Rl24._school_compute(self.company, self.y1)
        self.assertEqual(statements.amount, 690.0, "an issued statement is never rewritten")
        statements.invalidate_recordset(["amount_now"])
        self.assertEqual(statements.amount_now, 920.0)
        self.assertEqual(self.Rl24.search([("is_outdated", "=", True)]), statements)
        statements.with_user(self.office).action_update()
        self.assertEqual((statements.state, statements.amount), ("draft", 920.0))

    def test_not_a_year_to_come(self):
        with freeze_time(date(self.y1, 8, 15)):
            with self.assertRaises(UserError):
                self.Rl24._school_compute(self.company, self.y1 + 1)

    def test_amounts_are_the_computation_s(self):
        self._pay_on(date(self.y1, 12, 1), self._invoice(self._billed(), self.mom), 980.0)
        with freeze_time(date(self.y2, 1, 20)):
            statement = self.Rl24._school_compute(self.company, self.y1)
        with self.assertRaises(UserError):
            statement.with_user(self.office).amount = 5000.0
        with self.assertRaises(AccessError):
            statement.with_user(self.teacher).read(["amount"])

    def test_an_issued_statement_is_never_deleted(self):
        mom = self._invoice(self._billed(), self.mom)
        self._pay_on(date(self.y1, 12, 1), mom, 980.0)
        with freeze_time(date(self.y2, 1, 20)):
            statement = self.Rl24._school_compute(self.company, self.y1)
            statement.action_issue()
            mom.line_ids.filtered(lambda l: l.display_type == "payment_term").remove_move_reconcile()
            statement.action_update()
            self.Rl24._school_compute(self.company, self.y1)
        self.assertTrue(statement.exists(), "it was transmitted: a corrected slip needs it")
        self.assertEqual((statement.state, statement.amount), ("draft", 0.0))

    def test_a_statement_is_born_to_review(self):
        # 🔴 The computation runs in sudo: Odoo drops the office's `default_*` context there, not
        # their own ir.default, and a statement born "issued" reached the family's portal.
        self._pay_on(date(self.y1, 12, 1), self._invoice(self._billed(), self.mom), 980.0)
        for field, value in (("state", "issued"), ("was_issued", True), ("issued_on", "%s-01-15" % self.y2)):
            self.env["ir.default"].set("bf.school.rl24", field, value, user_id=self.office.id)
        with freeze_time(date(self.y2, 1, 20)):
            statement = self.Rl24.with_user(self.office)._school_compute(self.company, self.y1)
        self.assertEqual((statement.state, statement.was_issued, statement.issued_on), ("draft", False, False))

    def test_only_the_computation_creates_a_statement(self):
        # The model refuses before the access right is even read (create is not granted either):
        # the message says which one answered (AccessError is a UserError).
        with self.assertRaisesRegex(UserError, "computation of its year"):
            self.Rl24.with_user(self.office).create({
                "company_id": self.company.id, "year": self.y1, "partner_id": self.mom.id,
                "student_id": self.youngest.id, "amount": 9999.0, "state": "issued"})

    def test_the_export_is_its_author_s_and_runs_no_formula(self):
        self.mom.street = "=HYPERLINK(\"http://example.invalid\")"
        self._pay_on(date(self.y1, 12, 1), self._invoice(self._billed(), self.mom), 980.0)
        with freeze_time(date(self.y2, 1, 20)):
            self.Rl24._school_compute(self.company, self.y1).action_issue()
            wizard = self.env["bf.school.rl24.compute"].with_user(self.office).create({"year": self.y1})
            action = wizard.action_export()
        attachment = self.env["ir.attachment"].browse(int(action["url"].split("/")[3].split("?")[0]))
        self.assertIn("'=HYPERLINK", attachment.raw.decode("utf-8-sig"))
        other = new_test_user(self.env, login="school_rl24_office2", groups="bf_school_core.group_school_manager")
        with self.assertRaises(AccessError):
            wizard.with_user(other).read(["year"])
        with self.assertRaises(AccessError):
            attachment.with_user(other).read(["raw"])

    def test_export_lists_the_issued_ones(self):
        contract = self._billed()
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, self.mom), 980.0)
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, self.dad), 490.0)
        with freeze_time(date(self.y2, 1, 20)):
            statements = self.Rl24._school_compute(self.company, self.y1)
            statements.filtered(lambda s: s.partner_id == self.mom).action_issue()   # dad's waits
            wizard = self.env["bf.school.rl24.compute"].with_user(self.office).create({"year": self.y1})
            action = wizard.action_export()
        attachment = self.env["ir.attachment"].browse(int(action["url"].split("/")[3].split("?")[0]))
        content = attachment.raw.decode("utf-8-sig")
        self.assertIn("Maman Essai", content)
        self.assertIn("920.00", content)
        self.assertNotIn("Papa Essai", content)


@tagged("post_install", "-at_install")
class TestRl24Portal(HttpCase, Rl24Case):

    def test_a_payer_reads_her_own_statements_only(self):
        contract = self._billed()
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, self.mom), 980.0)
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, self.dad), 490.0)
        with freeze_time(date(self.y2, 1, 20)):
            statements = self.Rl24._school_compute(self.company, self.y1)
            statements.action_issue()
        new_test_user(self.env, login="school_rl24_mom", groups="base.group_portal",
                      partner_id=self.mom.id, password="school_rl24_mom")
        self.authenticate("school_rl24_mom", "school_rl24_mom")
        page = self.url_open("/my/school/rl24")
        self.assertEqual(page.status_code, 200)
        # The table only: the page's random CSRF token once held "460".
        table = page.text.split("o_school_rl24", 1)[1].split("</table>", 1)[0]
        self.assertIn("920", table)
        self.assertNotIn("460", table, "dad's statement is his")
        mine = statements.filtered(lambda s: s.partner_id == self.mom)
        theirs = statements - mine
        self.assertEqual(self.url_open("/my/school/rl24/%s/pdf" % mine.id).status_code, 200)
        self.assertEqual(self.url_open("/my/school/rl24/%s/pdf" % theirs.id).status_code, 404)

    def test_the_button_shows_to_whoever_has_a_statement(self):
        # Child care bills the first adult with parental authority when nobody is ticked « Pays »:
        # that adult gets a statement, and the button to it.
        contract = self._billed()
        self._pay_on(date(self.y1, 12, 1), self._invoice(contract, self.mom), 980.0)
        with freeze_time(date(self.y2, 1, 20)):
            self.Rl24._school_compute(self.company, self.y1).action_issue()
        self.env["bf.school.guardian.link"].sudo().search([("guardian_id", "in", (self.mom | self.dad).ids)]).write(
            {"is_payer": False})
        new_test_user(self.env, login="school_rl24_mom2", groups="base.group_portal",
                      partner_id=self.mom.id, password="school_rl24_mom2")
        self.authenticate("school_rl24_mom2", "school_rl24_mom2")
        self.assertIn('href="/my/school/rl24"', self.url_open("/my/school").text)
        new_test_user(self.env, login="school_rl24_dad2", groups="base.group_portal",
                      partner_id=self.dad.id, password="school_rl24_dad2")
        self.authenticate("school_rl24_dad2", "school_rl24_dad2")
        self.assertNotIn('href="/my/school/rl24"', self.url_open("/my/school").text, "no statement, not ticked")
