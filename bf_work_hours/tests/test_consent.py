from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import WorkHoursCase, acting_as


@tagged("post_install", "-at_install")
class TestConsent(WorkHoursCase):

    def test_off_by_default(self):
        self.assertFalse(self.alice.bf_work_hours_enabled)
        self.assertFalse(self.alice.bf_work_insights_enabled)
        self.assertFalse(self.Consent.sudo().search([("user_id", "=", self.alice.id)]))

    def test_the_person_turns_it_on_through_the_notice(self):
        consent = self.activate(self.alice)
        self.assertTrue(consent.is_active)
        self.assertIn("profile", consent.notice_text)
        self.assertTrue(self.alice.with_user(self.alice).bf_work_hours_enabled)

    def test_notice_must_be_ticked(self):
        wizard = self.env["bf.work.activate.wizard"].with_user(self.alice).create({"scope": "hours"})
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_an_administrator_cannot_turn_it_on_for_someone_else(self):
        Consent = self.Consent.with_user(self.admin)
        with self.assertRaises(AccessError):
            Consent.create({"user_id": self.alice.id, "scope": "hours"})
        # 🔴 The guard on vals is bypassed by a context default: the record is judged as created.
        with self.assertRaises(AccessError):
            Consent.with_context(default_user_id=self.alice.id).create({"scope": "hours"})
        with self.assertRaises(AccessError):
            self.alice.with_user(self.admin).action_bf_work_activate()
        self.assertFalse(self.Consent.sudo().search([("user_id", "=", self.alice.id)]))

    def test_no_backdated_or_prewithdrawn_activation(self):
        Consent = self.Consent.with_user(self.alice)
        with self.assertRaises(AccessError):
            Consent.with_context(default_granted_at="2020-01-01 00:00:00").create({"scope": "hours"})
        with self.assertRaises(AccessError):
            Consent.with_context(default_withdrawn_at="2026-01-01 00:00:00").create({"scope": "hours"})

    def test_nobody_reads_another_persons_activation(self):
        self.activate(self.alice)
        self.assertFalse(self.Consent.with_user(self.admin).search([("user_id", "=", self.alice.id)]))
        self.assertFalse(self.Consent.with_user(self.bob).search([]))

    def test_withdraw_once_and_keep_the_evidence(self):
        consent = self.activate(self.alice)
        with self.assertRaises(UserError):
            consent.with_user(self.alice).write({"scope": "insights"})
        with self.assertRaises(UserError):
            consent.with_user(self.alice).unlink()
        self.alice.with_user(self.alice).with_context(bf_work_scope="hours").action_bf_work_withdraw()
        self.assertFalse(consent.is_active)
        with self.assertRaises(UserError):
            consent.with_user(self.alice).write({"withdrawn_at": False})
        self.assertTrue(consent.exists(), "The activation is kept as evidence.")

    def test_an_administrator_cannot_turn_it_off_for_someone_else(self):
        self.activate(self.alice)
        with self.assertRaises(AccessError):
            self.alice.with_user(self.admin).with_context(bf_work_scope="hours").action_bf_work_withdraw()

    def test_indicators_need_hours_first(self):
        wizard = self.env["bf.work.activate.wizard"].with_user(self.alice).create(
            {"scope": "insights", "understood": True})
        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.activate(self.alice)
        self.activate(self.alice, "insights")
        self.assertTrue(self.alice.with_user(self.alice).bf_work_insights_enabled)

    def test_turning_hours_off_destroys_everything(self):
        self.activate(self.alice)
        self.activate(self.alice, "insights")
        self.signal(self.alice, self.utc(2026, 10, 9, 14), 30)
        self.Share.with_user(self.alice).create({"viewer_id": self.bob.id})
        self.alice.with_user(self.alice).with_context(bf_work_scope="hours").action_bf_work_withdraw()
        self.assertFalse(self.Signal.sudo().search([("user_id", "=", self.alice.id)]))
        self.assertFalse(self.Day.sudo().search([("user_id", "=", self.alice.id)]))
        self.assertFalse(self.Share.sudo().search([("owner_id", "=", self.alice.id), ("active", "=", True)]))
        self.assertFalse(self.Consent._bf_active_user_ids("insights") & {self.alice.id},
                         "Turning hours off turns the indicators off too.")


@tagged("post_install", "-at_install")
class TestConsentGuards(WorkHoursCase):
    """The guards of an activation, each with its test."""

    def test_nobody_else_reads_the_state_on_res_users(self):
        """🔴 Every internal or portal user reads res.users: the fields say nothing about others."""
        self.activate(self.alice)
        self.activate(self.alice, "insights")
        self.Share.with_user(self.alice).create({"viewer_id": self.bob.id})
        portal = self.env["res.users"].create({"name": "WH portal", "login": "wh_portal",
                                               "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        fields_ = ["bf_work_hours_enabled", "bf_work_insights_enabled", "bf_work_shared_owner_ids"]
        for reader in (self.bob, self.admin, portal):
            try:
                row = self.alice.with_user(reader).read(fields_)[0]
            except AccessError:
                continue  # cannot read the user at all: nothing leaks either
            self.assertFalse(row["bf_work_hours_enabled"] or row["bf_work_insights_enabled"], reader.login)
        self.assertFalse(self.bob.with_user(self.admin).read(["bf_work_shared_owner_ids"])[0]["bf_work_shared_owner_ids"])
        own = self.alice.with_user(self.alice).read(fields_)[0]
        self.assertTrue(own["bf_work_hours_enabled"] and own["bf_work_insights_enabled"])
        self.assertEqual(self.bob.with_user(self.bob).bf_work_shared_owner_ids, self.alice)
        # 🟠 Under an impersonation, the impersonator reads nothing either.
        with acting_as(self.alice.id, bf_impersonate={"from_uid": self.admin.id}):
            self.alice.invalidate_recordset(fields_)
            row = self.alice.with_user(self.alice).read(fields_)[0]
            self.assertFalse(row["bf_work_hours_enabled"] or row["bf_work_insights_enabled"])
        with acting_as(self.bob.id, bf_impersonate={"from_uid": self.admin.id}):
            self.bob.invalidate_recordset(fields_)
            self.assertFalse(self.bob.with_user(self.bob).bf_work_shared_owner_ids)

    def test_not_through_an_api_key(self):
        """🟠 An agent holding the person's API key cannot turn it on, nor off."""
        with acting_as(self.alice.id, "/xmlrpc/2/object"):
            with self.assertRaises(AccessError):
                self.Consent.with_user(self.alice).create({"scope": "hours"})
        consent = self.activate(self.alice)
        with acting_as(self.alice.id, "/json/2/bf.work.consent/write"):
            with self.assertRaises(AccessError):
                consent.with_user(self.alice).write({"withdrawn_at": "2026-10-09 12:00:00"})
        with acting_as(self.alice.id, "/web/dataset/call_kw"):
            consent.with_user(self.alice).write({"withdrawn_at": "2026-10-09 12:00:00"})
        self.assertFalse(consent.is_active)

    def test_not_while_being_impersonated(self):
        """🔴 Whoever impersonates the person can neither turn it on nor off for them."""
        with acting_as(self.alice.id, bf_impersonate={"origin": self.admin.id}):
            with self.assertRaises(AccessError):
                self.Consent.with_user(self.alice).create({"scope": "hours"})
            with self.assertRaises(AccessError):
                self.alice.with_user(self.alice).action_bf_work_activate()
        consent = self.activate(self.alice)
        with acting_as(self.alice.id, bf_impersonate={"origin": self.admin.id}):
            with self.assertRaises(AccessError):
                self.withdraw(self.alice)
            with self.assertRaises(AccessError):
                self.Share.with_user(self.alice).create({"viewer_id": self.admin.id})
        self.assertTrue(consent.is_active)
        for model in ("bf.work.consent", "bf.work.signal", "bf.work.day", "bf.work.share", "bf.work.activate.wizard"):
            self.assertEqual(getattr(self.env.registry[model], "_gen_scope", None), "bf_work_hours", model)

    def test_a_direct_withdrawal_destroys_too(self):
        """🟠 Withdrawing by a plain write destroys as the button does, and is dated now."""
        consent = self.activate(self.alice)
        insights = self.activate(self.alice, "insights")
        self.signal(self.alice, self.utc(2026, 10, 9, 14), 30)
        self.Day.sudo().create({"user_id": self.alice.id, "date": "2026-10-09", "hours": 1})
        consent.with_user(self.alice).write({"withdrawn_at": "2030-01-01 00:00:00"})
        self.assertLess(consent.withdrawn_at.year, 2030, "Dated now, not when the caller says.")
        self.assertFalse(insights.is_active, "The indicators go with the hours.")
        self.assertFalse(self.Signal.sudo().search([("user_id", "=", self.alice.id)]))
        self.assertFalse(self.Day.sudo().search([("user_id", "=", self.alice.id)]))

    def test_the_notice_is_the_servers(self):
        """🟠 The stored notice is the one the server writes, not one a client sends."""
        consent = self.Consent.with_user(self.alice).create({"scope": "hours", "notice_text": "<p>nothing</p>"})
        self.assertIn("profile", consent.notice_text)
        with self.assertRaises(UserError):
            self.Consent.with_user(self.bob).create({"scope": "insights"})

    def test_my_choices_page(self):
        """The page in the app shows the reader's own state and goes through the same guards."""
        Choices = self.env["bf.work.choices"]
        spec = {"display_name": {}, "hours_enabled": {}, "insights_enabled": {}}
        opened = Choices.with_user(self.alice).onchange({}, [], spec)["value"]
        self.assertFalse(opened["hours_enabled"])
        self.assertTrue(opened["display_name"], "The page has a title, not \"New\".")
        # A button saves the page before it runs: this saved page is what reloads.
        saved = Choices.with_user(self.alice).create({})
        self.assertFalse(saved.hours_enabled)
        action = saved.action_activate_hours()
        self.assertEqual(action["res_model"], "bf.work.activate.wizard")
        self.assertTrue(action["context"].get("bf_work_back_to_choices"))
        self.activate(self.alice)
        # 🔴 Seen in the browser: the saved page said "off" after the activation.
        saved.invalidate_recordset()
        self.assertTrue(saved.hours_enabled, "The saved page reads the state again.")
        # The state comes from the reader, never from the page; and a colleague
        # cannot read someone's page or notice at all.
        with self.assertRaises(AccessError):
            saved.with_user(self.bob).read(["hours_enabled"])
        notice_of_alice = self.env["bf.work.activate.wizard"].with_user(self.alice).create({"scope": "insights"})
        with self.assertRaises(AccessError):
            notice_of_alice.with_user(self.bob).read(["scope", "understood"])
        with self.assertRaises(AccessError):
            notice_of_alice.with_user(self.bob).write({"scope": "hours"})
        # The notice, opened from the page, brings the person back to a fresh page.
        notice = self.env["bf.work.activate.wizard"].with_user(self.alice).with_context(
            bf_work_back_to_choices=True).create({"scope": "insights", "understood": True})
        page_id = self.env.ref("bf_work_hours.action_bf_work_choices").id
        # A full reload: it starts the browser activity service again (it stops on "off").
        self.assertEqual(notice.action_confirm(), {"type": "ir.actions.client", "tag": "reload",
                                                   "params": {"action_id": page_id}})
        # What the form shows when it opens: the first onchange of a new record.
        self.assertTrue(Choices.with_user(self.alice).onchange({}, [], spec)["value"]["hours_enabled"])
        page = Choices.with_user(self.alice).create({})
        self.assertTrue(page.hours_enabled)
        self.assertFalse(Choices.with_user(self.bob).create({}).hours_enabled, "Bob's page is Bob's.")
        with acting_as(self.alice.id, "/xmlrpc/2/object"):
            with self.assertRaises(AccessError):
                page.action_withdraw_hours()
        back = page.action_withdraw_hours()
        self.assertFalse(self.Consent._bf_active_user_ids("hours") & {self.alice.id})
        # Back to a fresh page, without a record number in the address.
        self.assertEqual(back, {"type": "ir.actions.client", "tag": "reload", "params": {"action_id": page_id}})
        saved.invalidate_recordset()
        self.assertFalse(saved.hours_enabled)
