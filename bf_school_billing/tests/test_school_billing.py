from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class BillingCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({
            "name": "École des Essais", "billing_installment_count": 10,
            "sibling_rule_ids": [(0, 0, {"from_rank": 3, "percent": 15.0})]})
        # Next school year, whatever today is: billed before its services begin.
        y = date.today().year + 1
        cls.start = date(y, 9, 1)
        cls.year = env["bf.school.year"].create({
            "name": "%s-%s" % (y, y + 1), "school_id": cls.school.id,
            "date_start": cls.start, "date_end": date(y + 1, 6, 30)})
        cls.k5 = env.ref("bf_school_core.level_k5")
        cls.p3 = env.ref("bf_school_core.level_e3")
        cls.p5 = env.ref("bf_school_core.level_e5")
        cls.sport = env["bf.school.profile"].create({"name": "Sport-études", "school_id": cls.school.id})
        Group = env["bf.school.group"]
        cls.g_k5 = Group.create({"name": "Les Hiboux", "school_id": cls.school.id, "year_id": cls.year.id,
                                 "level_ids": [(6, 0, cls.k5.ids)]})
        cls.g_p35 = Group.create({"name": "Multi 3-5", "school_id": cls.school.id, "year_id": cls.year.id,
                                  "level_ids": [(6, 0, (cls.p3 | cls.p5).ids)]})
        Partner = env["res.partner"]
        cls.mom = Partner.create({"name": "Maman Essai", "email": "bil.mom@example.invalid", "lang": "fr_CA"
                                  if env["res.lang"]._lang_get("fr_CA") else "en_US"})
        cls.dad = Partner.create({"name": "Papa Essai", "email": "bil.dad@example.invalid"})
        cls.fund = Partner.create({"name": "Fondation de l'école", "is_company": True})
        # Three children of the same payers, the eldest first by birth date.
        cls.eldest = Partner.create({"name": "Aîné Essai", "is_student": True, "student_birthdate": date(2016, 1, 5)})
        cls.middle = Partner.create({"name": "Cadet Essai", "is_student": True, "student_birthdate": date(2018, 4, 9)})
        cls.youngest = Partner.create({"name": "Benjamin Essai", "is_student": True,
                                       "student_birthdate": date(2021, 2, 1)})
        Link = env["bf.school.guardian.link"]
        for kid in (cls.eldest, cls.middle, cls.youngest):
            Link.create({"student_id": kid.id, "guardian_id": cls.mom.id, "is_payer": True})
            Link.create({"student_id": kid.id, "guardian_id": cls.dad.id, "is_payer": True})
        Enrollment = env["bf.school.enrollment"]
        cls.e_eldest = Enrollment.create({"student_id": cls.eldest.id, "group_id": cls.g_p35.id,
                                          "level_id": cls.p5.id, "profile_id": cls.sport.id})
        cls.e_middle = Enrollment.create({"student_id": cls.middle.id, "group_id": cls.g_p35.id,
                                          "level_id": cls.p3.id})
        cls.e_youngest = Enrollment.create({"student_id": cls.youngest.id, "group_id": cls.g_k5.id})
        Fee = env["bf.school.fee"]
        cls.f_tuition = Fee.create({"name": "Scolarité", "year_id": cls.year.id, "category": "educational",
                                    "amount": 5000.0})
        cls.f_k5 = Fee.create({"name": "Maternelle", "year_id": cls.year.id, "category": "educational",
                               "amount": 0.0, "level_ids": [(6, 0, cls.k5.ids)], "rl24": "childcare"})
        cls.f_sport = Fee.create({"name": "Sport-études", "year_id": cls.year.id, "category": "educational",
                                  "amount": 1500.0, "profile_ids": [(6, 0, cls.sport.ids)],
                                  "sibling_discount": False})
        cls.f_daycare = Fee.create({"name": "Service de garde", "year_id": cls.year.id, "category": "accessory",
                                    "amount": 1200.0, "sibling_discount": False, "rl24": "childcare",
                                    "level_ids": [(6, 0, cls.k5.ids)]})
        cls.f_material = Fee.create({"name": "Matériel", "year_id": cls.year.id, "category": "accessory",
                                     "amount": 300.0, "sibling_discount": False})
        cls.f_bus = Fee.create({"name": "Transport", "year_id": cls.year.id, "category": "option",
                                "amount": 1390.0, "sibling_discount": False})
        # 🔴 The Administration alone, as a school sets it up: no Invoicing (it opens the
        # employees' bank accounts), so everything the office does goes through the school.
        cls.office = new_test_user(env, login="school_bil_office", groups="bf_school_core.group_school_manager")
        cls.teacher = new_test_user(env, login="school_bil_teacher", groups="bf_school_core.group_school_user")

    def _prepare(self):
        self.year.with_user(self.office).action_school_prepare_contracts()
        return {c.student_id: c for c in self.env["bf.school.contract"].search([("year_id", "=", self.year.id)])}

    def _signed(self, contract):
        contract.sudo().state = "signed"
        return contract

    def _pay(self, move, amount=None):
        # 🔴 Odoo 18 offers the NEXT INSTALMENT by default: the whole balance is asked for.
        vals = {"amount": amount or move.amount_residual}
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=move.ids).create(vals)._create_payments()


@tagged("post_install", "-at_install")
class TestCatalogue(BillingCase):

    def test_level_follows_a_single_level_group(self):
        self.assertEqual(self.e_youngest.level_id, self.k5)
        other = self.env["bf.school.enrollment"].create({
            "student_id": self.env["res.partner"].create({"name": "Sans niveau", "is_student": True}).id,
            "group_id": self.g_p35.id})
        self.assertFalse(other.level_id, "a multi-level group does not choose for the office")
        with self.assertRaises(ValidationError):
            other.level_id = self.k5

    def test_contracts_priced_from_the_catalogue(self):
        contracts = self._prepare()
        self.assertEqual(len(contracts), 3)
        eldest, middle, youngest = contracts[self.eldest], contracts[self.middle], contracts[self.youngest]
        self.assertEqual(eldest.tuition, 6500.0, "tuition and the sport profile")
        self.assertEqual(middle.tuition, 5000.0)
        self.assertEqual(youngest.sibling_rank, 3)
        self.assertEqual(youngest.tuition, 4250.0, "15 % from the third child")
        self.assertEqual(youngest.tuition_rl24, 0.0, "the kindergarten fee is 0 here")
        self.assertEqual(youngest.accessory_ids.mapped("name"), ["Service de garde", "Matériel"])
        self.assertEqual(eldest.accessory_ids.mapped("name"), ["Matériel"])
        self.assertFalse(eldest.accessory_ids.filtered(lambda a: a.fee_id == self.f_bus),
                         "an optional service waits for the family's choice")
        self.assertEqual(eldest.installment_count, 10)
        self.assertEqual((eldest.date_start, eldest.date_end), (self.year.date_start, self.year.date_end))
        self.assertEqual(self._prepare(), contracts, "a second run adds nothing")

    def test_kindergarten_tuition_is_child_care(self):
        self.f_k5.amount = 4000.0
        self.f_tuition.level_ids = self.p3 | self.p5
        youngest = self._prepare()[self.youngest]
        self.assertEqual(youngest.tuition, 3400.0)
        self.assertEqual(youngest.tuition_rl24, 3400.0, "pre-K and kindergarten count in full")

    def test_a_student_without_level_gets_no_contract(self):
        stray = self.env["res.partner"].create({"name": "Sans niveau", "is_student": True})
        self.env["bf.school.enrollment"].create({"student_id": stray.id, "group_id": self.g_p35.id})
        result = self.year.with_user(self.office).action_school_prepare_contracts()
        self.assertIn("Sans niveau", result["params"]["message"])
        self.assertFalse(self.env["bf.school.contract"].search([("student_id", "=", stray.id)]))

    def test_one_contract_refused_does_not_stop_the_others(self):
        # A 200 $ registration fee is above a tenth of a 1 500 $ price (Regulation r. 3, s. 12).
        self.f_tuition.amount = 1200.0
        campaign = self.env["bf.school.admission.campaign"].create({
            "name": "Réinscription", "kind": "reenrollment", "school_id": self.school.id,
            "target_year": self.year.name, "fee_amount": 200.0,
            "date_open": date(2026, 2, 1), "date_close": date(2026, 3, 1)})
        self.env["bf.school.admission"].create({
            "campaign_id": campaign.id, "student_id": self.middle.id, "state": "confirmed"})
        result = self.year.with_user(self.office).action_school_prepare_contracts()
        self.assertIn("Cadet Essai", result["params"]["message"])
        contracts = self.env["bf.school.contract"].search([("year_id", "=", self.year.id)])
        self.assertEqual(contracts.student_id, self.eldest | self.youngest)

    def test_reprice_keeps_the_options_chosen(self):
        contract = self._prepare()[self.middle].with_user(self.office)
        contract.accessory_ids = [(0, 0, {"fee_id": self.f_bus.id, "name": "Transport", "price": 1390.0})]
        contract.profile_id = self.sport
        contract.action_school_reprice()
        self.assertEqual(contract.tuition, 6500.0)
        self.assertEqual(sorted(contract.accessory_ids.mapped("name")), ["Matériel", "Transport"])

    def test_fees_and_payers_are_the_office_s(self):
        for model in ("bf.school.fee", "bf.school.billing.share", "bf.school.sibling.rule"):
            with self.assertRaises(AccessError):
                self.env[model].with_user(self.teacher).search([])

    def test_a_fee_is_not_negative(self):
        with self.assertRaises(ValidationError):
            self.f_material.amount = -1


@tagged("post_install", "-at_install")
class TestPayers(BillingCase):

    def test_both_payers_share_equally(self):
        contract = self._prepare()[self.middle]
        self.assertEqual(contract.share_ids.partner_id, self.mom | self.dad)
        self.assertEqual(contract.share_ids.mapped("percent"), [50.0, 50.0])
        self.assertEqual(sorted(contract.share_ids.mapped("billed_amount")), [2650.0, 2650.0])

    def test_a_bursary_pays_first(self):
        contract = self._prepare()[self.middle].with_user(self.office)
        contract.share_ids = [(0, 0, {"partner_id": self.fund.id, "kind": "bursary", "amount": 1000.01})]
        amounts = contract._school_share_amounts()
        self._signed(contract).action_school_bill()
        fund = contract.share_ids.filtered(lambda s: s.kind == "bursary").sudo().invoice_id
        self.assertEqual(len(fund.invoice_line_ids), 1, "the fund needs its share, not the family's options")
        self.assertFalse(fund.invoice_line_ids.school_rl24)
        self.assertEqual(amounts[contract.share_ids.filtered(lambda s: s.kind == "bursary").id], 1000.01)
        family = sorted(amounts[s.id] for s in contract.share_ids if s.kind == "family")
        self.assertEqual(family, [2149.99, 2150.0], "the cent of rounding goes somewhere, once")
        self.assertAlmostEqual(sum(amounts.values()), 5300.0, places=2)

    def test_shares_must_add_up(self):
        contract = self._prepare()[self.middle]
        contract.share_ids[0].percent = 60.0
        with self.assertRaises(UserError):
            contract._school_share_amounts()
        contract.share_ids[0].percent = 50.0
        contract.share_ids = [(0, 0, {"partner_id": self.fund.id, "kind": "bursary", "amount": 9999.0})]
        with self.assertRaises(UserError):
            contract._school_share_amounts()


@tagged("post_install", "-at_install")
class TestInvoices(BillingCase):

    def test_office_alone_bills_the_payers(self):
        contract = self._signed(self._prepare()[self.youngest]).with_user(self.office)
        contract.action_school_bill()
        self.assertTrue(contract.is_billed)
        shares = contract.share_ids
        self.assertTrue(all(shares.mapped("invoice_name")), "the office reads the invoice numbers")
        invoices = shares.sudo().invoice_id
        self.assertEqual(invoices.partner_id, self.mom | self.dad)
        self.assertEqual(set(invoices.mapped("state")), {"posted"})
        self.assertAlmostEqual(sum(invoices.mapped("amount_total")), contract.total_price, places=2)
        self.assertFalse(invoices.invoice_user_id, "the school sends them, not the person who clicked")
        care = invoices.invoice_line_ids.filtered("school_rl24")
        self.assertAlmostEqual(sum(care.mapped("price_subtotal")), 1200.0, places=2)
        with self.assertRaises(UserError):
            contract.action_school_bill()

    def test_due_dates_are_the_contract_s(self):
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        invoice = contract.share_ids[0].invoice_id
        terms = invoice.line_ids.filtered(lambda l: l.display_type == "payment_term").sorted("date_maturity")
        self.assertEqual(len(terms), 10)
        due_dates = [d for d, _a in contract._installments()]
        self.assertEqual(terms.mapped("date_maturity"), due_dates)
        self.assertGreaterEqual(terms[0].date_maturity, contract.date_start,
                                "nothing due before the services begin (Act s. 70)")
        self.assertAlmostEqual(sum(terms.mapped("balance")), invoice.amount_total, places=2)

    def test_billed_late_the_past_instalments_are_due_now(self):
        contract = self._prepare()[self.middle]
        start = fields.Date.today().replace(day=1) - relativedelta(months=2)
        contract.write({"date_start": start, "date_end": start + relativedelta(months=10, days=-1)})
        self._signed(contract).action_school_bill()
        invoice = contract.share_ids[0].invoice_id
        terms = invoice.line_ids.filtered(lambda l: l.display_type == "payment_term").sorted("date_maturity")
        past = [d for d, _a in contract._installments() if d <= invoice.invoice_date]
        self.assertEqual(len(past), 3)
        self.assertEqual(len(terms), 10 - len(past) + 1)
        self.assertEqual(terms[0].date_maturity, invoice.invoice_date)
        self.assertAlmostEqual(terms[0].balance, 3 * 265.0, places=2, msg="three instalments due now")

    def test_admission_fee_due_now_when_not_yet_invoiced(self):
        contract = self._prepare()[self.middle]
        contract.admission_fee = 200.0
        self._signed(contract).action_school_bill()
        invoice = contract.share_ids[0].invoice_id
        terms = invoice.line_ids.filtered(lambda l: l.display_type == "payment_term").sorted("date_maturity")
        self.assertEqual(len(terms), 11)
        self.assertEqual(terms[0].date_maturity, invoice.invoice_date)
        self.assertAlmostEqual(terms[0].balance, 100.0, places=2, msg="half of the fee, for half the share")

    def test_registration_fee_of_the_reenrolment_not_billed_twice(self):
        campaign = self.env["bf.school.admission.campaign"].create({
            "name": "Réinscription", "kind": "reenrollment", "school_id": self.school.id,
            "target_year": self.year.name, "fee_amount": 200.0,
            "date_open": date(2026, 2, 1), "date_close": date(2026, 3, 1)})
        app = self.env["bf.school.admission"].create({
            "campaign_id": campaign.id, "student_id": self.middle.id, "payer_id": self.mom.id,
            "guardian_ids": [(6, 0, self.mom.ids)]})
        registration = app._school_create_fee_invoice()
        contract = self._prepare()[self.middle]
        self.assertEqual(contract.admission_fee, 200.0, "the registration fee is in the price")
        self._signed(contract).action_school_bill()
        invoices = contract.share_ids.invoice_id
        self.assertAlmostEqual(sum(invoices.mapped("amount_total")), contract.total_price - 200.0, places=2)
        self.assertEqual(registration.school_contract_id, contract)
        self._pay(registration)
        self.assertEqual(contract.amount_paid, 200.0, "the registration fee paid counts on the contract")

    def test_amount_paid_follows_the_payments(self):
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        self._pay(contract.share_ids[0].invoice_id, 530.0)
        self.assertEqual(contract.amount_paid, 530.0)
        self.assertEqual(contract.share_ids[0].invoice_paid, 530.0)
        with self.assertRaises(UserError):
            contract.with_user(self.office).amount_paid = 9999.0

    def test_a_payer_s_invoice_is_set_by_the_billing(self):
        contracts = self._prepare()
        contract, other = contracts[self.middle], self._signed(contracts[self.eldest])
        other.action_school_bill()
        with self.assertRaises(UserError):
            contract.share_ids[0].with_user(self.office).write({"invoice_id": other.share_ids[0].invoice_id.id})

    def test_payers_frozen_once_billed(self):
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        with self.assertRaises(UserError):
            contract.share_ids[0].with_user(self.office).percent = 70.0
        with self.assertRaises(UserError):
            contract.share_ids[0].with_user(self.office).invoice_id = False

    def test_draft_contract_is_not_billed(self):
        contract = self._prepare()[self.middle]
        with self.assertRaises(UserError):
            contract.with_user(self.office).action_school_bill()

    def test_signature_asks_the_office_to_bill(self):
        # 🔴 Billing on the last signature emailed a year of invoices before anyone checked the
        # payers: the signature leaves the office an activity.
        contract = self._prepare()[self.middle]
        action = contract.with_user(self.office).action_send_for_signature()
        request = self.env["bf.sign.request"].browse(action["res_id"])
        request.signer_ids.sudo().write({"state": "signed"})
        contract._sign_on_signed(request)
        self.assertEqual(contract.state, "signed")
        self.assertFalse(contract.is_billed)
        self.assertEqual(contract.activity_ids.user_id, self.office)
        self.assertEqual(contract.share_ids.partner_id, self.mom | self.dad)

    def test_nobody_follows_an_invoice_but_its_payer(self):
        # 🔴 A parent who signed on the portal was subscribed to the other parent's invoice:
        # its email and its portal page.
        portal_mom = new_test_user(self.env, login="school_bil_mom", groups="base.group_portal",
                                   partner_id=self.mom.id)
        contract = self._signed(self._prepare()[self.middle])
        contract.with_user(portal_mom).sudo()._school_create_invoices()
        for share in contract.share_ids:
            self.assertEqual(share.invoice_id.message_partner_ids, share.partner_id)
        other = self._signed(self._prepare()[self.eldest])
        other.with_user(self.office).action_school_bill()
        self.assertNotIn(self.office.partner_id, other.share_ids.invoice_id.message_partner_ids)

    def test_a_contract_is_not_created_signed(self):
        with self.assertRaises(UserError):
            self.env["bf.school.contract"].with_user(self.office).create({
                "student_id": self.middle.id, "school_id": self.school.id, "year_id": self.year.id,
                "date_start": self.start, "date_end": self.year.date_end, "tuition": 100.0,
                "installment_count": 2, "state": "signed"})
        self.env["bf.school.contract"].with_user(self.office).create({
            "student_id": self.middle.id, "school_id": self.school.id, "year_id": self.year.id,
            "date_start": self.start, "date_end": self.year.date_end, "tuition": 100.0,
            "installment_count": 2, "state": "draft"})

    def test_registration_fee_outside_the_price_stops_the_billing(self):
        contract = self._signed(self._prepare()[self.middle])   # prepared before the re-enrolment
        campaign = self.env["bf.school.admission.campaign"].create({
            "name": "Réinscription", "kind": "reenrollment", "school_id": self.school.id,
            "target_year": self.year.name, "fee_amount": 200.0,
            "date_open": date(2026, 2, 1), "date_close": date(2026, 3, 1)})
        app = self.env["bf.school.admission"].create({
            "campaign_id": campaign.id, "student_id": self.middle.id, "payer_id": self.mom.id,
            "guardian_ids": [(6, 0, self.mom.ids)]})
        app._school_create_fee_invoice()
        with self.assertRaises(UserError):
            contract.with_user(self.office).action_school_bill()
        app.action_withdraw()
        self.assertFalse(contract._school_registration_invoice(), "a withdrawn re-enrolment owes nothing")

    def test_a_contract_invoice_is_corrected_by_a_credit_note(self):
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        with self.assertRaises(UserError):
            contract.share_ids[0].invoice_id.button_draft()

    def test_a_write_off_is_not_money(self):
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        invoice = contract.share_ids.filtered(lambda s: s.partner_id == self.dad).invoice_id
        self._pay(invoice, 650.0)
        receivable = invoice.line_ids.filtered(lambda l: l.display_type == "payment_term")[:1].account_id
        expense = self.env["account.account"].search([("account_type", "=", "expense"),
                                                       ("company_ids", "in", invoice.company_id.ids)], limit=1)
        bad_debt = self.env["account.move"].create({
            "move_type": "entry", "date": fields.Date.today(), "line_ids": [
                (0, 0, {"account_id": receivable.id, "partner_id": self.dad.id, "credit": 2000.0}),
                (0, 0, {"account_id": expense.id, "debit": 2000.0})]})
        bad_debt.action_post()
        (invoice.line_ids | bad_debt.line_ids).filtered(
            lambda l: l.account_id == receivable and not l.reconciled).reconcile()
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assertEqual(contract.amount_paid, 650.0, "the bad debt settled the invoice, nobody paid it")

    def test_a_credit_note_applied_to_another_invoice_is_a_payment(self):
        a = self._signed(self._prepare()[self.middle])
        b = self._signed(self.env["bf.school.contract"].search([("student_id", "=", self.eldest.id)]))
        (a | b).action_school_bill()
        mom_a = a.share_ids.filtered(lambda s: s.partner_id == self.mom).invoice_id
        mom_b = b.share_ids.filtered(lambda s: s.partner_id == self.mom).invoice_id
        self._pay(mom_a)
        a.termination_date = self.start + relativedelta(months=1, days=14)
        a.action_terminate()
        refund = self.env["account.move"].search([("school_contract_id", "=", a.id),
                                                  ("partner_id", "=", self.mom.id), ("move_type", "=", "out_refund")])
        # Instead of a cheque, the 1 870 $ go to the eldest's invoice.
        (refund.line_ids | mom_b.line_ids).filtered(
            lambda l: l.display_type == "payment_term" and not l.reconciled).reconcile()
        self.assertEqual(refund.amount_residual, 0.0)
        self.assertEqual(a.amount_paid, 780.0, "the money left the terminated contract")
        self.assertEqual(b.amount_paid, 1870.0, "and paid the other one")

    def test_a_credit_note_typed_by_hand_is_not_money(self):
        # 🔴 A credit note made from scratch (no invoice reversed) settles an invoice: nobody paid.
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        invoice = contract.share_ids.filtered(lambda s: s.partner_id == self.dad).invoice_id
        self._pay(invoice, 650.0)
        refund = self.env["account.move"].create({
            "move_type": "out_refund", "partner_id": self.dad.id,
            "invoice_line_ids": [(0, 0, {"name": "Geste commercial", "quantity": 1, "price_unit": 500.0,
                                         "tax_ids": [(5, 0, 0)]})]})
        refund.action_post()
        self.assertFalse(refund.reversed_entry_id)
        (refund.line_ids | invoice.line_ids).filtered(
            lambda l: l.display_type == "payment_term" and not l.reconciled).reconcile()
        self.assertEqual(refund.amount_residual, 0.0)
        self.assertEqual(contract.amount_paid, 650.0, "the credit note settled part of the invoice, nobody paid it")
        both = (invoice | refund)._school_paid_amount()
        self.assertEqual(both, 650.0, "read together, the credit note neither adds nor takes away money")

    def test_a_payer_without_email_is_told_to_the_office(self):
        self.dad.email = False
        contract = self._signed(self._prepare()[self.middle])
        contract.with_user(self.office).action_school_bill()
        self.env["bf.school.contract"]._cron_send_invoices()
        dad = contract.share_ids.filtered(lambda s: s.partner_id == self.dad).invoice_id
        mom = contract.share_ids.filtered(lambda s: s.partner_id == self.mom).invoice_id
        self.assertTrue(dad.school_billing_sent and not dad.is_move_sent, "not sent, and not said sent")
        self.assertTrue(mom.school_billing_sent and mom.is_move_sent)
        self.assertIn("no email address", contract.activity_ids.summary)

    def test_an_invoice_sent_by_hand_is_not_sent_again(self):
        # 🔴 Sent from the accounting after a failed run, then by the scheduled action: twice.
        contract = self._signed(self._prepare()[self.middle])
        contract.with_user(self.office).action_school_bill()
        mom = contract.share_ids.filtered(lambda s: s.partner_id == self.mom).invoice_id
        dad = contract.share_ids.filtered(lambda s: s.partner_id == self.dad).invoice_id
        self.env["account.move.send"]._generate_and_send_invoices(mom, sending_methods=["email"])
        dad.is_move_sent = True   # what Odoo ticks on a PDF downloaded without any email
        before = {m: len(m.message_ids) for m in mom | dad}
        self.env["bf.school.contract"]._cron_send_invoices()
        self.assertEqual(len(mom.message_ids), before[mom], "not sent a second time")
        self.assertGreater(len(dad.message_ids), before[dad], "downloaded is not sent")
        self.assertTrue(mom.school_billing_sent and dad.school_billing_sent)


@tagged("post_install", "-at_install")
class TestTermination(BillingCase):
    """5 300 $ over ten months from Sept. 1, half each. Terminated on Oct. 15: two months of
    services (1 060 $) and the penalty (min(500 $, 530 $)), so the school keeps 1 560 $."""

    def _terminated_after_mom_paid(self):
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        mom = contract.share_ids.filtered(lambda s: s.partner_id == self.mom).invoice_id
        dad = contract.share_ids.filtered(lambda s: s.partner_id == self.dad).invoice_id
        self._pay(mom)
        contract.termination_date = self.start + relativedelta(months=1, days=14)
        contract.with_user(self.office).action_terminate()
        return contract, mom, dad

    def test_termination_credits_what_the_school_may_not_keep(self):
        contract, mom, dad = self._terminated_after_mom_paid()
        self.assertEqual(contract.termination_due, 1560.0)
        refunds = self.env["account.move"].search([("school_contract_id", "=", contract.id),
                                                    ("move_type", "=", "out_refund")])
        self.assertEqual(refunds.partner_id, self.mom | self.dad)
        self.assertEqual(sum(refunds.mapped("amount_total")), 3740.0)
        self.assertEqual(dad.amount_residual, 780.0, "dad still owes his half of what the school keeps")
        self.assertEqual(refunds.filtered(lambda r: r.partner_id == self.mom).amount_residual, 1870.0,
                         "mom gets back what she paid beyond her half")
        self.assertEqual(contract.termination_refund, 1870.0, "what the credit notes owe, not a net")
        activity = contract.activity_ids
        self.assertEqual(activity.date_deadline, contract.termination_date + relativedelta(days=10),
                         "within ten days (Act s. 74)")
        self.assertIn("Maman Essai", activity.note)

    def test_paid_back_lowers_the_amount_paid(self):
        contract, _mom, _dad = self._terminated_after_mom_paid()
        self.assertEqual(contract.amount_paid, 2650.0)
        refund = self.env["account.move"].search([("school_contract_id", "=", contract.id),
                                                  ("partner_id", "=", self.mom.id),
                                                  ("move_type", "=", "out_refund")])
        self._pay(refund)
        self.assertEqual(refund.amount_residual, 0.0)
        self.assertEqual(contract.amount_paid, 780.0)

    def test_a_write_off_is_not_refunded(self):
        # 🔴 Dad paid 1 000 $ and the rest was written off; his half of what the school keeps is
        # 780 $: 220 $ go back to him, not the 1 870 $ of a credit note on a settled invoice.
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        dad = contract.share_ids.filtered(lambda s: s.partner_id == self.dad).invoice_id
        self._pay(dad, 1000.0)
        receivable = dad.line_ids.filtered(lambda l: l.display_type == "payment_term")[:1].account_id
        expense = self.env["account.account"].search([("account_type", "=", "expense"),
                                                       ("company_ids", "in", dad.company_id.ids)], limit=1)
        bad_debt = self.env["account.move"].create({
            "move_type": "entry", "date": fields.Date.today(), "line_ids": [
                (0, 0, {"account_id": receivable.id, "partner_id": self.dad.id, "credit": 1650.0}),
                (0, 0, {"account_id": expense.id, "debit": 1650.0})]})
        bad_debt.action_post()
        (dad.line_ids | bad_debt.line_ids).filtered(
            lambda l: l.account_id == receivable and not l.reconciled).reconcile()
        self.assertEqual(dad.amount_residual, 0.0)
        contract.termination_date = self.start + relativedelta(months=1, days=14)
        contract.with_user(self.office).action_terminate()
        refunds = self.env["account.move"].search([("school_contract_id", "=", contract.id),
                                                    ("move_type", "=", "out_refund")])
        self.assertEqual(refunds.filtered(lambda r: r.partner_id == self.dad).amount_residual, 220.0)
        self.assertEqual(contract.termination_refund, 220.0, "mom's credit note settles what she owed")
        self.assertIn("220", contract.activity_ids.note)
        self.assertIn("check it before refunding", contract.activity_ids.note, "the write-off is told")

    def _billed(self):
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        pick = lambda who: contract.share_ids.filtered(lambda s: s.partner_id == who).invoice_id
        return contract, pick(self.mom), pick(self.dad)

    def test_a_correction_paid_back_is_not_refunded_again(self):
        # 🔴 Mom paid 2 650 $, then got 500 $ back on a correction credit note: she paid 2 150 $
        # and keeps 780 $ owed, so 1 370 $ go back to her, not 1 870 $.
        contract, mom, _dad = self._billed()
        self._pay(mom)
        correction = self.env["account.move"].create({
            "move_type": "out_refund", "partner_id": self.mom.id, "reversed_entry_id": mom.id,
            "invoice_line_ids": [(0, 0, {"name": "Correction", "quantity": 1, "price_unit": 500.0})]})
        correction.action_post()
        self._pay(correction)
        self.assertEqual(correction.amount_residual, 0.0)
        self.assertEqual(contract.amount_paid, 2150.0, "what the correction paid back comes off")
        contract.termination_date = self.start + relativedelta(months=1, days=14)
        contract.with_user(self.office).action_terminate()
        self.assertEqual(contract.termination_refund, 1370.0)
        refund = self.env["account.move"].search([("school_contract_id", "=", contract.id),
                                                  ("partner_id", "=", self.mom.id), ("move_type", "=", "out_refund")])
        self.assertEqual(refund.amount_residual, 1370.0)

    def test_a_payment_typed_on_the_bank_is_money(self):
        # 🔴 Read as a write-off, dad's 1 000 $ typed as an entry on the bank were never refunded.
        contract, _mom, dad = self._billed()
        receivable = dad.line_ids.filtered(lambda l: l.display_type == "payment_term")[:1].account_id
        bank = self.env["account.journal"].search([("type", "=", "bank"),
                                                   ("company_id", "=", dad.company_id.id)], limit=1)
        entry = self.env["account.move"].create({
            "move_type": "entry", "journal_id": bank.id, "date": fields.Date.today(), "line_ids": [
                (0, 0, {"account_id": receivable.id, "partner_id": self.dad.id, "credit": 1000.0}),
                (0, 0, {"account_id": bank.default_account_id.id, "debit": 1000.0})]})
        entry.action_post()
        (dad.line_ids | entry.line_ids).filtered(
            lambda l: l.account_id == receivable and not l.reconciled).reconcile()
        self.assertEqual(contract.amount_paid, 1000.0)
        contract.termination_date = self.start + relativedelta(months=1, days=14)
        contract.with_user(self.office).action_terminate()
        self.assertEqual(contract.termination_refund, 220.0)
        self.assertNotIn("check it before refunding", contract.activity_ids.note)


@tagged("post_install", "-at_install")
class TestGuardsAtCreation(BillingCase):
    """What the billing sets is not forged at creation either: by the values, by `default_*` in
    the context, or by the user's own ir.default."""

    def _draft_vals(self, **extra):
        return dict({"student_id": self.middle.id, "school_id": self.school.id, "year_id": self.year.id,
                     "date_start": self.start, "date_end": self.year.date_end, "tuition": 100.0,
                     "installment_count": 2, "state": "draft"}, **extra)

    def _personal_default(self, model, field, value):
        self.env["ir.default"].set(model, field, value, user_id=self.office.id)

    def test_a_contract_is_not_created_billed(self):
        Contract = self.env["bf.school.contract"].with_user(self.office)
        with self.assertRaisesRegex(UserError, "billing sets this"):
            Contract.create(self._draft_vals(billed_on=fields.Datetime.now()))
        forged = {"billed_on": fields.Datetime.now(), "registration_billed": 100.0,
                  "sibling_rank": 3, "sibling_percent": 50.0}
        by_context = Contract.with_context(**{"default_" + f: v for f, v in forged.items()}).create(
            self._draft_vals())
        for field, value in forged.items():
            self._personal_default("bf.school.contract", field, value)
        by_default = Contract.create(self._draft_vals())
        for contract in (by_context, by_default):
            self.assertEqual((contract.billed_on, contract.registration_billed, contract.sibling_rank,
                              contract.sibling_percent), (False, 0.0, 0, 0.0))
        # Prepared from the catalogue, the billing still sets them.
        prepared = self._prepare()[self.youngest]
        self.assertEqual((prepared.billed_on, prepared.sibling_rank, prepared.sibling_percent), (False, 3, 15.0))

    def test_the_screen_s_empty_values_pass(self):
        # 🔴 A guard at creation of the form `set(vals) & RESERVED` also
        # refused the empty values the web client sends with a new record, and "Save" failed on
        # the screen while the Python tests passed. Only a forged value is refused.
        contract = self.env["bf.school.contract"].with_user(self.office).create(self._draft_vals(
            billed_on=False, registration_billed=0.0, sibling_rank=0, sibling_percent=0.0))
        self.assertEqual((contract.state, contract.billed_on, contract.sibling_rank), ("draft", False, 0))
        share = self.env["bf.school.billing.share"].with_user(self.office).create({
            "contract_id": contract.id, "partner_id": self.fund.id, "kind": "bursary", "amount": 1.0,
            "invoice_id": False})
        self.assertFalse(share.invoice_id)
        accountant = new_test_user(self.env, login="school_bil_screen", groups="account.group_account_invoice")
        move = self.env["account.move"].with_user(accountant).create({
            "move_type": "out_invoice", "partner_id": self.mom.id, "school_contract_id": False,
            "school_billing_sent": False, "invoice_line_ids": [(0, 0, {
                "name": "Repas", "quantity": 1, "price_unit": 20.0, "school_rl24": False})]})
        self.assertFalse(move.school_contract_id or move.school_billing_sent or any(move.line_ids.mapped("school_rl24")))

    def test_a_payer_is_not_created_on_an_invoice(self):
        # 🔴 On a live invoice, the payer was refused by luck (its contract looked billed); on a
        # cancelled one, it passed.
        contract = self._prepare()[self.middle]
        cancelled = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.mom.id,
            "invoice_line_ids": [(0, 0, {"name": "Autre", "quantity": 1, "price_unit": 10.0})]})
        cancelled.button_cancel()
        Share = self.env["bf.school.billing.share"].with_user(self.office)
        vals = {"contract_id": contract.id, "partner_id": self.fund.id, "kind": "bursary", "amount": 1.0}
        with self.assertRaisesRegex(UserError, "invoice of a payer is set by the billing"):
            Share.create(dict(vals, invoice_id=cancelled.id))
        by_context = Share.with_context(default_invoice_id=cancelled.id).create(vals)
        self.assertFalse(by_context.invoice_id)
        by_context.unlink()
        self._personal_default("bf.school.billing.share", "invoice_id", cancelled.id)
        self.assertFalse(Share.create(vals).invoice_id)

    def test_a_payer_does_not_move_to_a_billed_contract(self):
        billed = self._signed(self._prepare()[self.middle])
        billed.with_user(self.office).action_school_bill()
        draft = self._prepare()[self.eldest]
        share = self.env["bf.school.billing.share"].with_user(self.office).create({
            "contract_id": draft.id, "partner_id": self.fund.id, "kind": "bursary", "amount": 1.0})
        with self.assertRaisesRegex(UserError, "already invoiced"):
            share.write({"contract_id": billed.id})

    def test_invoices_and_credit_notes_are_born_unsent(self):
        # 🔴 In sudo, Odoo drops the office's `default_*` context, not their own ir.default.
        self._personal_default("account.move", "school_billing_sent", True)
        contract = self._signed(self._prepare()[self.middle])
        contract.with_user(self.office).action_school_bill()
        contract.termination_date = self.start + relativedelta(months=1, days=14)
        contract.with_user(self.office).action_terminate()
        moves = contract.sudo().school_move_ids
        self.assertEqual(set(moves.mapped("move_type")), {"out_invoice", "out_refund"})
        self.assertFalse(any(moves.mapped("school_billing_sent")))

    def test_invoices_ignore_the_clicker_s_own_defaults(self):
        # 🔴 In sudo, the office's own ir.default went into the invoices and the credit notes:
        # posted at 0 $, in another currency, with a forged payment note.
        usd = self.env.ref("base.USD")
        self._personal_default("account.move.line", "discount", 100.0)
        self._personal_default("account.move", "currency_id", usd.id)
        self._personal_default("account.move", "narration", "<p>Payez ailleurs</p>")
        self._personal_default("account.payment.term", "early_discount", True)
        contract = self._signed(self._prepare()[self.middle])
        contract.with_user(self.office).action_school_bill()
        invoices = contract.sudo().share_ids.invoice_id
        self.assertEqual(len(invoices), 2)
        for share in contract.sudo().share_ids:
            invoice = share.invoice_id
            self.assertEqual(invoice.amount_total, share.billed_amount)
            self.assertEqual(invoice.currency_id, contract.currency_id)
            self.assertFalse(any(invoice.invoice_line_ids.mapped("discount")))
            self.assertFalse(invoice.narration)
            self.assertFalse(invoice.invoice_payment_term_id.early_discount)
        contract.termination_date = self.start + relativedelta(months=1, days=14)
        contract.with_user(self.office).action_terminate()
        refunds = contract.sudo().school_move_ids.filtered(lambda m: m.move_type == "out_refund")
        self.assertEqual(sum(refunds.mapped("amount_total")),
                         sum(invoices.mapped("amount_total")) - contract.termination_due)
        self.assertFalse(any(refunds.invoice_line_ids.mapped("discount")))

    def test_invoices_ignore_the_context_of_a_caller_in_sudo(self):
        # 🔴 Called by code already in sudo, the superuser environment kept the caller's context:
        # its default_* went into the invoices.
        contract = self._signed(self._prepare()[self.middle])
        contract.sudo().with_context(default_narration="<p>Payez ailleurs</p>", default_ref="Forgé",
                                     default_invoice_date_due="2020-01-01").action_school_bill()
        invoices = contract.sudo().share_ids.invoice_id
        self.assertEqual(len(invoices), 2)
        for invoice in invoices:
            self.assertFalse(invoice.narration)
            self.assertNotEqual(invoice.ref, "Forgé")

    def test_an_entry_made_in_a_session_is_no_contract_s(self):
        # 🔴 An invoice made in sudo in a user's session (re-enrolment fee, meal top-up) took
        # their own ir.default: tied to another child's contract, ticked child care.
        contract = self._signed(self._prepare()[self.middle])
        contract.action_school_bill()
        self._personal_default("account.move", "school_contract_id", contract.id)
        self._personal_default("account.move", "school_billing_sent", True)
        self._personal_default("account.move.line", "school_rl24", True)
        move = self.env(user=self.office.id, su=True)["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.mom.id,
            "invoice_line_ids": [(0, 0, {"name": "Repas", "quantity": 1, "price_unit": 20.0})]})
        self.assertFalse(move.school_contract_id or move.school_billing_sent)
        self.assertFalse(any(move.line_ids.mapped("school_rl24")))
        # An Invoicing user neither creates one nor moves one.
        accountant = new_test_user(self.env, login="school_bil_accountant", groups="account.group_account_invoice")
        Move = self.env["account.move"].with_user(accountant)
        with self.assertRaisesRegex(UserError, "billing sets this"):
            Move.create({"move_type": "out_invoice", "partner_id": self.mom.id, "school_billing_sent": True})
        invoice = contract.sudo().share_ids.invoice_id[:1].with_user(accountant)
        other = self._signed(self._prepare()[self.eldest])
        for vals in ({"school_contract_id": other.id}, {"school_contract_id": False}, {"school_billing_sent": True}):
            with self.assertRaisesRegex(UserError, "billing sets this"):
                invoice.write(vals)
        with self.assertRaisesRegex(UserError, "billing sets this"):
            invoice.invoice_line_ids[:1].write({"school_rl24": True})
        # 🔴 At creation too: a line born ticked on a posted contract invoice fed the Relevé 24.
        # Unticked, the accounting's own lines are not the billing's business.
        Line = self.env["account.move.line"].with_user(accountant).with_context(check_move_validity=False)
        line_vals = {"move_id": invoice.id, "name": "Ajout", "quantity": 1, "price_unit": 1.0,
                     "account_id": invoice.invoice_line_ids[:1].account_id.id, "display_type": "product"}
        with self.assertRaisesRegex(UserError, "billing sets this"):
            Line.create(dict(line_vals, school_rl24=True))
        self.assertFalse(Line.create(dict(line_vals, school_rl24=False)).school_rl24)
        invoice.write({"ref": "Vu"})   # anything else stays the accounting's

    def test_payers_and_contracts_born_in_sudo_are_not_billed(self):
        # 🔴 The payers born at the signature (sudo, in the signer's session) took their own
        # ir.default: another family's invoice, credited at the termination.
        other = self._signed(self._prepare()[self.eldest])
        other.action_school_bill()
        foreign = other.sudo().share_ids.invoice_id[:1]
        self._personal_default("bf.school.billing.share", "invoice_id", foreign.id)
        self._personal_default("bf.school.contract", "billed_on", "2026-09-01 00:00:00")
        session = self.env(user=self.office.id, su=True)
        contract = session["bf.school.contract"].create(self._draft_vals())
        self.assertFalse(contract.billed_on)
        contract._school_default_shares()
        self.assertEqual(len(contract.share_ids), 2)
        self.assertFalse(contract.share_ids.invoice_id)
