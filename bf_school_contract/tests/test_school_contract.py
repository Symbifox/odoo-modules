from datetime import date

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestSchoolContract(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais", "contract_art14": True})
        cls.year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 9, 1), "date_end": date(2027, 6, 30)})
        Partner = env["res.partner"]
        cls.child = Partner.create({"name": "Enfant Essai", "is_student": True})
        cls.mom = Partner.create({"name": "Maman Essai", "email": "ctr.mom@example.invalid"})
        cls.dad = Partner.create({"name": "Papa Essai", "email": "ctr.dad@example.invalid"})
        cls.step = Partner.create({"name": "Beau-parent Essai", "email": "ctr.step@example.invalid"})
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.child.id, "guardian_id": cls.mom.id, "is_payer": True})
        Link.create({"student_id": cls.child.id, "guardian_id": cls.dad.id})
        Link.create({"student_id": cls.child.id, "guardian_id": cls.step.id,
                     "has_parental_authority": False, "can_sign": False})
        cls.teacher = new_test_user(env, login="school_ctr_teacher", groups="bf_school_core.group_school_user")

    def _contract(self, **vals):
        return self.env["bf.school.contract"].create(dict({
            "student_id": self.child.id, "school_id": self.school.id, "year_id": self.year.id,
            "date_start": self.year.date_start, "date_end": self.year.date_end,
            "admission_fee": 200.0, "tuition": 5000.0,
            "accessory_ids": [(0, 0, {"name": "Surveillance du midi", "price": 800.0})],
            "installment_count": 10}, **vals))

    def test_office_alone_sends_for_signature(self):
        office = new_test_user(self.env, login="school_ctr_office", groups="bf_school_core.group_school_manager")
        contract = self._contract().with_user(office)
        action = contract.action_send_for_signature()
        request = self.env["bf.sign.request"].browse(action["res_id"])
        self.assertEqual(request.signer_ids.mapped("email"), ["ctr.mom@example.invalid", "ctr.dad@example.invalid"])

    def test_total_price_includes_the_admission_fee(self):
        self.assertEqual(self._contract().total_price, 6000.0)

    def test_signers_are_the_guardians_who_sign(self):
        self.assertEqual(self._contract().client_ids, self.mom | self.dad)

    def test_eligibility_fee_cap(self):
        self._contract(eligibility_fee=50.0)
        with self.assertRaises(ValidationError):
            self._contract(eligibility_fee=50.01)

    def test_admission_fee_cap_is_200_or_a_tenth(self):
        with self.assertRaises(ValidationError):
            self._contract(admission_fee=200.01)
        # 1 000 $ of tuition and 120 $ of admission: the tenth of 1 120 $ is 112 $.
        with self.assertRaises(ValidationError):
            self._contract(tuition=1000.0, accessory_ids=[], admission_fee=120.0)
        self._contract(tuition=1000.0, accessory_ids=[], admission_fee=110.0)

    def test_at_least_two_instalments(self):
        with self.assertRaises(ValidationError):
            self._contract(installment_count=1)

    def test_instalments_split_the_balance(self):
        contract = self._contract(installment_count=3)
        schedule = contract._installments()
        self.assertEqual(len(schedule), 3)
        self.assertAlmostEqual(sum(a for _d, a in schedule), 5800.0, places=2)
        self.assertEqual(schedule[0][0], contract.date_start, "nothing due before the services begin")

    def test_max_penalty(self):
        # min(500 $, 600 $) - 200 $ = 300 $
        self.assertEqual(self._contract().max_penalty, 300.0)
        # total 1 110 $: min(500 $, 111 $) - 110 $ = 1 $
        self.assertEqual(self._contract(tuition=1000.0, accessory_ids=[], admission_fee=110.0).max_penalty, 1.0)

    def test_termination_before_the_services(self):
        contract = self._contract()
        for day in (date(2026, 8, 15), date(2026, 5, 1), date(2026, 1, 10)):
            self.assertEqual(contract._termination_amounts(day), 500.0,
                             "admission fee plus the indemnity, however early (s. 72)")

    def test_termination_after_three_months(self):
        contract = self._contract()
        contract.sudo().state = "signed"
        contract.write({"termination_date": date(2026, 11, 20), "amount_paid": 3100.0})
        contract.action_terminate()
        # 200 $ + 5 800 $ x 3/10 + 300 $ = 2 240 $; paid 3 100 $: 860 $ back within ten days.
        self.assertEqual(contract.termination_due, 2240.0)
        self.assertEqual(contract.termination_refund, 860.0)
        self.assertEqual(contract.refund_deadline, date(2026, 11, 30))
        self.assertEqual(contract.state, "terminated")

    def test_document_carries_the_mandatory_mentions(self):
        contract = self._contract()
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_school_contract.report_school_contract", contract.ids)[0].decode()
        for text in ("Le client peut, à tout moment et à sa discrétion, résilier le contrat",
                     "Dans les dix jours qui suivent la résiliation du contrat",
                     "L'établissement s'engage à ne pas céder ou vendre le présent contrat",
                     "l'obligation d'avoir le visage découvert",
                     "Sauf dans le cas d'une bourse",
                     "le ministre fait lui-même ce remboursement à même le",
                     "Surveillance du midi", "Maman Essai", "Papa Essai"):
            self.assertIn(text, html)
        self.assertNotIn("Beau-parent Essai", html)

    def test_send_for_signature_to_every_signer(self):
        contract = self._contract()
        contract.action_send_for_signature()
        request = self.env["bf.sign.request"].search(
            [("res_model", "=", contract._name), ("res_id", "=", contract.id)])
        self.assertEqual(len(request), 1)
        self.assertEqual(request.signer_ids.partner_id, self.mom | self.dad)
        with self.assertRaises(UserError):
            contract.action_send_for_signature()  # one request at a time
        contract._sign_on_signed(request)
        self.assertEqual(contract.state, "draft", "nobody has signed yet")
        request.signer_ids.filtered(lambda s: s.partner_id == self.mom).sudo().write({"state": "signed"})
        contract._sign_on_signed(request)
        self.assertEqual(contract.state, "draft", "one parent of two is not the contract signed")
        request.signer_ids.sudo().write({"state": "signed"})
        contract._sign_on_signed(request)
        self.assertEqual(contract.state, "signed")
        with self.assertRaises(UserError):
            contract.action_send_for_signature()

    def test_nobody_signs(self):
        contract = self._contract(client_ids=[(5, 0, 0)])
        with self.assertRaises(UserError):
            contract.action_send_for_signature()

    def test_teachers_have_no_access(self):
        contract = self._contract()
        with self.assertRaises(AccessError), mute_logger("odoo.models"):
            contract.with_user(self.teacher).read(["total_price"])

    # Adversarial review (2026-09-27)
    def test_state_is_not_written_by_hand(self):
        office = new_test_user(self.env, login="school_ctr_rpc", groups="bf_school_core.group_school_manager")
        contract = self._contract().with_user(office)
        for vals in ({"state": "signed"}, {"signed_on": "2026-09-01 10:00:00"}, {"termination_refund": 0}):
            with self.assertRaises(UserError):
                contract.write(vals)

    def test_signed_terms_are_frozen(self):
        office = new_test_user(self.env, login="school_ctr_rpc2", groups="bf_school_core.group_school_manager")
        contract = self._contract().with_user(office)
        contract.action_send_for_signature()
        with self.assertRaises(UserError):
            contract.write({"tuition": 1.0})
        with self.assertRaises(UserError):
            contract.accessory_ids[:1].write({"price": 0.0})

    def test_a_late_signature_does_not_revive_a_terminated_contract(self):
        contract = self._contract()
        contract.action_send_for_signature()
        request = self.env["bf.sign.request"].search([("res_model", "=", contract._name), ("res_id", "=", contract.id)])
        request.signer_ids.sudo().write({"state": "signed"})
        contract._sign_on_signed(request)
        contract.write({"termination_date": contract.date_start, "amount_paid": 0})
        contract.action_terminate()
        contract._sign_on_signed(request)
        self.assertEqual(contract.state, "terminated")

    def test_no_negative_amount(self):
        # Admission fee at 0: the cap of 1/10 of the price must not be what refuses it.
        with self.assertRaises(ValidationError):
            self._contract(tuition=-1.0, admission_fee=0.0)
        with self.assertRaises(ValidationError):
            self._contract(admission_fee=0.0, eligibility_fee=-10.0)
        with self.assertRaises(ValidationError):
            self._contract(accessory_ids=[(0, 0, {"name": "Rabais déguisé", "price": -100.0})])
