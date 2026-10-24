from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bf_work_hours.tests.common import WorkHoursCase, acting_as


@tagged("post_install", "-at_install")
class TestPrivacyRegister(WorkHoursCase):

    def test_activation_is_a_granted_consent(self):
        consent = self.activate(self.alice)
        mirror = consent.sudo().privacy_consent_id
        self.assertEqual(mirror.status, "granted")
        self.assertEqual(mirror.subject_partner_id, self.alice.partner_id)
        self.assertEqual(mirror.purpose_id, self.env.ref("bf_work_hours_privacy.purpose_work_hours"))
        self.assertFalse(mirror.expires_at)

    def test_the_register_says_who_sees_it(self):
        """The two purposes name the colleagues one shares with, in English and in French;
        the note of the consent says the person turned it on, in their own session."""
        for xmlid in ("bf_work_hours_privacy.purpose_work_hours", "bf_work_hours_privacy.purpose_work_insights"):
            purpose = self.env.ref(xmlid)
            self.assertIn("the colleagues you name", purpose.with_context(lang="en_US").plain_language_summary)
            if self.env["res.lang"]._lang_get("fr_CA"):
                self.assertIn("collègues que vous nommez",
                              purpose.with_context(lang="fr_CA").plain_language_summary)
        consent = self.activate(self.alice)
        note = consent.sudo().privacy_consent_id.notes
        # Written in the language of the session that turns it on.
        self.assertTrue("own session" in note or "propre session" in note, note)
        self.assertNotIn("preferences", note)
        self.assertNotIn("préférences", note)
        if self.env["res.lang"]._lang_get("fr_CA"):
            # 🔴 A code translation without "#. odoo-python" is not loaded: the note stayed English.
            wizard = self.env["bf.work.activate.wizard"].with_user(self.bob).with_context(lang="fr_CA").create(
                {"scope": "hours", "understood": True})
            wizard.action_confirm()
            mirror = self.Consent.sudo().search([("user_id", "=", self.bob.id), ("scope", "=", "hours"),
                                                 ("withdrawn_at", "=", False)]).privacy_consent_id
            self.assertIn("propre session", mirror.notes)

    def test_turning_off_in_preferences_withdraws_the_consent(self):
        hours = self.activate(self.alice)
        insights = self.activate(self.alice, "insights")
        self.alice.with_user(self.alice).with_context(bf_work_scope="hours").action_bf_work_withdraw()
        self.assertEqual(hours.sudo().privacy_consent_id.status, "withdrawn")
        self.assertEqual(insights.sudo().privacy_consent_id.status, "withdrawn")

    def test_withdrawing_in_the_register_turns_it_off_and_forgets(self):
        hours = self.activate(self.alice)
        self.activate(self.alice, "insights")
        self.signal(self.alice, self.utc(2026, 10, 9, 14), 30)
        hours.sudo().privacy_consent_id.with_user(self.alice).sudo().write({"status": "withdrawn"})
        self.assertFalse(hours.is_active)
        self.assertFalse(self.Consent._bf_active_user_ids("hours") & {self.alice.id})
        self.assertFalse(self.Consent._bf_active_user_ids("insights") & {self.alice.id})
        self.assertFalse(self.Signal.sudo().search([("user_id", "=", self.alice.id)]))

    def test_the_privacy_officer_cannot_turn_it_off(self):
        """🔴 The register is read by others: they cannot withdraw for the person."""
        hours = self.activate(self.alice)
        mirror = hours.sudo().privacy_consent_id
        with self.assertRaises(UserError):
            mirror.with_user(self.admin).sudo().write({"status": "withdrawn"})
        # Even the person cannot give it an expiry (the subject check passes, this one refuses).
        with self.assertRaises(UserError):
            mirror.with_user(self.alice).sudo().write({"expires_at": "2026-01-01 00:00:00"})
        self.assertTrue(hours.is_active)
        self.assertLessEqual(mirror.access_token_expires_at, fields.Datetime.now(), "No public link.")

    def test_the_person_withdraws_from_the_register(self):
        hours = self.activate(self.alice)
        hours.sudo().privacy_consent_id.with_user(self.alice).sudo().write({"status": "withdrawn"})
        self.assertFalse(hours.is_active)

    def test_the_notice_says_the_register_sees_the_activation(self):
        notice = self.env["bf.work.activate.wizard"]._bf_notice("hours")
        # The database's language: the source in English, or its fr_CA catalogue.
        self.assertTrue("privacy register" in notice or "registre de la vie privée" in notice, notice)



@tagged("post_install", "-at_install")
class TestPrivacyRegisterGuards(WorkHoursCase):
    """🔴 Nobody turns it off through the register but the person, in their session."""

    def setUp(self):
        super().setUp()
        self.hours = self.activate(self.alice)
        self.mirror = self.hours.sudo().privacy_consent_id

    def test_a_forged_context_does_not_open_the_mirror(self):
        """A privacy user sends the module's own context key by RPC: no superuser, no trust."""
        officer = self.env["res.users"].create({
            "name": "WH officer", "login": "wh_officer",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_manager").id])]})
        forged = self.mirror.with_user(officer).with_context(bf_work_from_work=True)
        with self.assertRaises(UserError):
            forged.write({"subject_partner_id": officer.partner_id.id})
        with self.assertRaises(UserError):
            forged.write({"status": "withdrawn"})
        with self.assertRaises(UserError):
            forged.write({"expires_at": "2027-01-01 00:00:00"})
        with self.assertRaises(UserError):
            forged.unlink()
        self.assertTrue(self.hours.is_active)
        self.assertEqual(self.mirror.status, "granted")

    def test_not_through_an_api_key_nor_an_impersonation(self):
        for path, session in (("/xmlrpc/2/object", {}), ("/my/privacy/consent/1/withdraw",
                                                         {"bf_impersonate": {"from_uid": self.admin.id}})):
            with acting_as(self.alice.id, path, **session):
                with self.assertRaises(UserError):
                    self.mirror.with_user(self.alice).sudo().write({"status": "withdrawn"})
        self.assertTrue(self.hours.is_active)

    def test_the_person_in_their_session_can(self):
        with acting_as(self.alice.id, "/my/privacy/consent/1/withdraw"):
            self.mirror.with_user(self.alice).sudo().write({"status": "withdrawn"})
        self.assertFalse(self.hours.is_active)

    def test_no_copy_no_renewal_no_deletion(self):
        with self.assertRaises(UserError):
            self.mirror.copy()
        with self.assertRaises(UserError):
            self.mirror.action_renew()
        with self.assertRaises(UserError):
            self.mirror.unlink()

    def test_a_withdrawal_from_the_register_withdraws_both_mirrors(self):
        """🟡 Withdrawing hours from the register leaves no "indicators: granted" behind."""
        insights = self.activate(self.alice, "insights")
        with acting_as(self.alice.id, "/my/privacy/consent/1/withdraw"):
            self.mirror.with_user(self.alice).sudo().write({"status": "withdrawn"})
        self.assertEqual(insights.sudo().privacy_consent_id.status, "withdrawn")

    def test_the_mirror_ignores_the_callers_defaults(self):
        """🟡 A default expiry or archive sent at activation does not reach the mirror."""
        self.withdraw(self.alice)
        consent = self.Consent.with_user(self.alice).with_context(
            default_expires_at="2027-01-01 00:00:00", default_active=False).create({"scope": "hours"})
        mirror = consent.sudo().privacy_consent_id
        self.assertFalse(mirror.expires_at)
        self.assertTrue(mirror.active)

    def test_the_mirror_ignores_an_administrators_default(self):
        """🟡 An ir.default set on the register (archived by default) does not reach the mirror."""
        self.withdraw(self.alice)
        self.env["ir.default"].set("privacy.consent", "active", False)
        consent = self.Consent.with_user(self.alice).create({"scope": "hours"})
        self.assertTrue(consent.sudo().privacy_consent_id.active)


@tagged("post_install", "-at_install")
class TestPrivacyEarlierDays(WorkHoursCase):
    """« My earlier days » is a consent of its own in the register too."""

    def take_in(self, user, span="30"):
        self.env["bf.work.activate.wizard"].with_user(user).create(
            {"scope": "history", "understood": True, "history_span": span}).action_confirm()
        return self.Consent.sudo().search([("user_id", "=", user.id), ("scope", "=", "history"),
                                           ("withdrawn_at", "=", False)])

    def test_its_own_purpose_and_mirror(self):
        hours = self.activate(self.alice)
        history = self.take_in(self.alice)
        mirror = history.privacy_consent_id
        self.assertEqual(mirror.purpose_id, self.env.ref("bf_work_hours_privacy.purpose_work_history"))
        self.assertEqual(mirror.status, "granted")
        self.assertNotEqual(mirror, hours.sudo().privacy_consent_id)
        self.assertTrue("privacy register" in history.notice_text
                        or "registre de la vie privée" in history.notice_text, "The register is named in it.")

    def test_withdrawn_from_the_register(self):
        hours = self.activate(self.alice)
        history = self.take_in(self.alice)
        history.privacy_consent_id.with_user(self.alice).sudo().write({"status": "withdrawn"})
        self.assertTrue(history.withdrawn_at)
        self.assertTrue(hours.is_active, "Taking back the earlier days leaves the hours on.")

    def test_the_hours_withdrawn_take_the_earlier_days_along(self):
        hours = self.activate(self.alice)
        history = self.take_in(self.alice)
        hours.sudo().privacy_consent_id.with_user(self.alice).sudo().write({"status": "withdrawn"})
        self.assertTrue(history.withdrawn_at)
        self.assertEqual(history.privacy_consent_id.status, "withdrawn")
