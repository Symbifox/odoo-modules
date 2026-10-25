"""Un congé de constance approuvé devient une allocation dans Congés."""
from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_gamification.tests.common import in_a_request


@tagged("post_install", "-at_install")
class TestConstanceAllocation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.user = new_test_user(cls.env, login="fqh_employe", name="Fox Employé")
        cls.employee = cls.env["hr.employee"].create({"name": "Fox Employé", "user_id": cls.user.id})
        cls.officer = new_test_user(
            cls.env, login="fqh_rh", name="Fox RH",
            groups="base.group_user,bf_gamification.group_gamification_manager,"
                   "hr_holidays.group_hr_holidays_user")
        cls.env["hr.employee"].create({"name": "Fox RH", "user_id": cls.officer.id})
        cls.fq_only = new_test_user(
            cls.env, login="fqh_gestion", name="Fox Gestion",
            groups="base.group_user,bf_gamification.group_gamification_manager")
        cls.leave_type = cls.env["hr.leave.type"].create({
            "name": "Constance", "requires_allocation": "yes",
            "allocation_validation_type": "hr",
        })
        cls.reward = cls.env.ref("bf_gamification.reward_leave_constance")

    def _claim(self):
        return self.env["bf.gamification.reward.claim"].sudo().with_context(
            bf_gamification_constance=True).create({
                "user_id": self.user.id, "reward_id": self.reward.id, "leave_days": 1.0})

    def _set_type(self, leave_type):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_gamification.leave_type_id", str(leave_type.id) if leave_type else "")

    def test_an_officer_s_approval_validates_the_allocation(self):
        self._set_type(self.leave_type)
        claim = self._claim()
        claim.with_user(self.officer).action_approve()
        allocation = claim.allocation_id
        self.assertEqual(claim.state, "approved")
        self.assertEqual((allocation.employee_id, allocation.number_of_days, allocation.state),
                         (self.employee, 1.0, "validate"))

    def test_without_time_off_rights_the_allocation_waits(self):
        self._set_type(self.leave_type)
        claim = self._claim()
        claim.with_user(self.fq_only).action_approve()
        self.assertEqual(claim.state, "approved")
        self.assertEqual(claim.allocation_id.state, "confirm")

    def test_no_leave_type_no_approval(self):
        self._set_type(False)
        claim = self._claim()
        with self.assertRaises(UserError):
            claim.with_user(self.officer).action_approve()
        self.assertEqual(claim.state, "pending")

    def test_refusing_after_approval_refuses_the_allocation(self):
        self._set_type(self.leave_type)
        claim = self._claim()
        claim.with_user(self.officer).action_approve()
        claim.with_user(self.officer).action_refuse()
        self.assertEqual(claim.allocation_id.state, "refuse")

    def test_approving_twice_creates_no_second_allocation(self):
        # L'approbation lit l'état courant.
        self._set_type(self.leave_type)
        claim = self._claim()
        claim.with_user(self.officer).action_approve()
        with self.assertRaises(UserError):
            claim.with_user(self.officer).action_approve()
        allocations = self.env["hr.leave.allocation"].search([
            ("employee_id", "=", self.employee.id), ("holiday_status_id", "=", self.leave_type.id)])
        self.assertEqual(len(allocations), 1)

    def test_time_off_validated_afterwards_repairs_the_streak(self):
        # Malade lundi et mardi, congé validé le mercredi : la constance que le
        # premier XP du mercredi avait cassée revient.
        Profile = self.env["bf.gamification.profile"]
        self.patch(type(Profile), "_bf_is_interactive", lambda model: True)
        self.user.tz = "America/Montreal"
        self.employee.tz = "America/Montreal"
        # Les gestes de la personne elle-même : seuls ils font des jours de
        # constance.
        profile = Profile.with_user(self.user)._get_or_create_profile(self.user)
        sick = self.env["hr.leave.type"].create({
            "name": "Maladie FQ", "requires_allocation": "no",
            "leave_validation_type": "hr", "time_type": "leave"})
        for stamp in ("2026-10-01 15:00:00", "2026-10-02 15:00:00"):   # jeudi, vendredi
            with freeze_time(stamp):
                profile._award_xp(1, "manual", "jour")
        with freeze_time("2026-10-07 15:00:00"):   # mercredi
            profile._award_xp(1, "manual", "mercredi")
            self.assertEqual(profile.current_streak, 1)
            leave = self.env["hr.leave"].create({
                "name": "FQ malade", "employee_id": self.employee.id,
                "holiday_status_id": sick.id,
                "request_date_from": "2026-10-05", "request_date_to": "2026-10-06"})
            leave.action_approve()
            self.assertEqual(leave.state, "validate")
            self.assertEqual(profile.current_streak, 3)

    def test_t2_the_allocation_of_a_request_is_set_by_the_bridge_only(self):
        # Un gestionnaire qui y mettait une autre
        # allocation empêchait la vraie à l'approbation.
        self._set_type(self.leave_type)
        claim = self._claim()
        other = self.env["hr.leave.allocation"].sudo().create({
            "name": "FQ étrangère", "holiday_status_id": self.leave_type.id,
            "employee_id": self.employee.id, "number_of_days": 3, "allocation_type": "regular"})
        with self.assertRaises(UserError):
            claim.with_user(self.officer).write({"allocation_id": other.id})
        claim.with_user(self.officer).action_approve()
        allocation = claim.allocation_id
        self.assertNotEqual(allocation, other)
        with self.assertRaises(UserError):
            claim.with_user(self.officer).write({"allocation_id": False})
        self.assertEqual(claim.allocation_id, allocation)

    def test_t3_2_the_allocation_follows_time_off_rights_whatever_the_context(self):
        # Créée en sudo, l'allocation prenait son état du
        # contexte de l'appelant ; `default_state=validate` sautait « la validation
        # suit les droits de Congés ».
        self._set_type(self.leave_type)
        claim = self._claim()
        claim.with_user(self.fq_only).with_context(default_state="validate").action_approve()
        self.assertEqual(claim.state, "approved")
        self.assertEqual(claim.allocation_id.state, "confirm", "sans droits de Congés, elle attend")
        # Par RPC, le cœur retire déjà les `default_*` au passage en sudo
        # (`Environment.__call__`) ; un appelant DÉJÀ en sudo (le code d'un autre
        # module, une action serveur) les garde : seul le nettoyage du pont les
        # retire alors.
        other = self._claim()
        other.with_user(self.fq_only).sudo().with_context(
            default_state="validate", default_number_of_days=9.0,
            default_date_from="2020-01-01").action_approve()
        allocation = other.allocation_id
        self.assertEqual((allocation.state, allocation.number_of_days), ("confirm", 1.0),
                         "même appelée en sudo, l'allocation suit les droits de Congés")
        self.assertNotEqual(str(allocation.date_from), "2020-01-01")

    def test_t3_3_approving_or_refusing_is_refused_under_impersonation(self):
        # Sous usurpation du support, la décision serait
        # prise sous l'identité de l'approbateur ; pas d'allocation non plus.
        self._set_type(self.leave_type)
        claim = self._claim()
        in_a_request(self, impersonated=True)
        with self.assertRaises(AccessError) as caught:
            claim.with_user(self.officer).action_approve()
        self.assertIn("usurpation", str(caught.exception))
        self.assertEqual((claim.state, claim.sudo().allocation_id.id), ("pending", False))
        # Revenue à son propre compte, l'approbatrice décide ; refuser sous
        # usurpation ne défait pas l'allocation non plus.
        in_a_request(self)
        claim.with_user(self.officer).action_approve()
        allocation = claim.allocation_id
        in_a_request(self, impersonated=True)
        with self.assertRaises(AccessError):
            claim.with_user(self.officer).action_refuse()
        self.assertEqual((claim.state, allocation.state), ("approved", "validate"))
