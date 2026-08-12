import base64
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestClawback(TransactionCase):
    """Clawback remediation: .eml ingestion, strategy selection, and the
    preview → execute → restore sweep with the IMAP backend mocked out (no
    network). Also asserts no message body is ever stored."""

    def setUp(self):
        super().setUp()
        # The acting user needs the purge privilege to execute/restore.
        self.env.user.groups_id = [(4, self.env.ref(
            "bf_security_awareness.group_bf_secaware_purge").id)]
        self.connector = self.env["bf.mail.clawback.connector"].create({
            "name": "Test", "backend": "m365_oauth",
            "mailbox_source": "manual",
            "manual_emails": "a@bf.test\nb@bf.test",
            "tenant_id": "t", "client_id": "c",
        })
        self.reporter = self.env["res.partner"].create(
            {"name": "Reporter", "email": "r@bf.test"})

    def _report(self, **kw):
        vals = {"reporter_id": self.reporter.id, "category": "phishing"}
        vals.update(kw)
        return self.env["bf.reported.phish"].create(vals)

    # -- .eml ingestion --------------------------------------------------
    def test_eml_parsing_fills_message_id_from_and_subject(self):
        eml = (b"Message-ID: <abc123@evil.test>\r\n"
               b"From: Bad Guy <bad@evil.test>\r\n"
               b"Subject: Win a prize\r\n\r\nbody")
        report = self._report()
        self.env["ir.attachment"].create({
            "name": "sample.eml",
            "res_model": "bf.reported.phish", "res_id": report.id,
            "datas": base64.b64encode(eml),
        })
        report.apply_eml_metadata()
        self.assertEqual(report.message_id, "<abc123@evil.test>")
        self.assertEqual(report.email_from, "bad@evil.test")
        self.assertEqual(report.subject, "Win a prize")

    # -- strategy selection ----------------------------------------------
    def test_launch_clawback_picks_message_id_strategy(self):
        report = self._report(
            message_id="<abc@evil.test>", email_from="bad@evil.test",
            subject="Win", state="threat_confirmed")
        action = report.action_launch_clawback()
        op = self.env["bf.clawback.operation"].browse(action["res_id"])
        self.assertEqual(op.match_strategy, "message_id")
        self.assertEqual(op.message_id, "<abc@evil.test>")
        self.assertEqual(op.reported_phish_id, report)

    def test_launch_clawback_falls_back_to_heuristic(self):
        report = self._report(
            email_from="bad@evil.test", subject="Win",
            state="threat_confirmed")
        action = report.action_launch_clawback()
        op = self.env["bf.clawback.operation"].browse(action["res_id"])
        self.assertEqual(op.match_strategy, "heuristic")
        self.assertTrue(op.since_date)

    # -- full sweep, IMAP mocked -----------------------------------------
    def test_preview_execute_restore_cycle(self):
        report = self._report(
            message_id="<m@evil.test>", state="threat_confirmed")
        action = report.action_launch_clawback()
        op = self.env["bf.clawback.operation"].browse(action["res_id"])
        cls = type(self.connector)
        with patch.object(cls, "_mint_token", return_value="tok"), \
                patch.object(cls, "_login", return_value=MagicMock()), \
                patch.object(cls, "_imap_search", return_value=[b"1", b"2"]), \
                patch.object(cls, "_imap_move", return_value=2):
            op.action_preview()
            self.assertEqual(op.state, "preview")
            self.assertEqual(op.mailboxes_swept, 2)
            self.assertEqual(op.messages_found, 4)  # 2 mailboxes x 2 hits
            self.assertEqual(op.messages_removed, 0)

            op.action_execute()
            self.assertEqual(op.state, "done")
            self.assertEqual(op.messages_removed, 4)
            self.assertTrue(op.launched_by)

            op.action_restore()
            self.assertEqual(op.state, "restored")

    def test_heuristic_execute_requires_preview(self):
        report = self._report(
            email_from="bad@evil.test", subject="Win",
            state="threat_confirmed")
        action = report.action_launch_clawback()
        op = self.env["bf.clawback.operation"].browse(action["res_id"])
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            op.action_execute()  # no preview yet -> blocked

    # -- hardening -------------------------------------------------------
    def test_delete_mode_requires_message_id(self):
        report = self._report(
            email_from="bad@evil.test", subject="Win", state="threat_confirmed")
        action = report.action_launch_clawback()
        op = self.env["bf.clawback.operation"].browse(action["res_id"])
        op.mode = "delete"  # heuristic + delete = forbidden
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            op.action_execute()

    def test_blast_cap_blocks_until_confirmed(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_security_awareness.clawback_max_blast_messages", "1")
        report = self._report(
            message_id="<m@evil.test>", state="threat_confirmed")
        action = report.action_launch_clawback()
        op = self.env["bf.clawback.operation"].browse(action["res_id"])
        cls = type(self.connector)
        from odoo.exceptions import UserError
        with patch.object(cls, "_mint_token", return_value="tok"), \
                patch.object(cls, "_login", return_value=MagicMock()), \
                patch.object(cls, "_imap_search", return_value=[b"1", b"2"]), \
                patch.object(cls, "_imap_move", return_value=2):
            op.action_preview()
            self.assertEqual(op.messages_found, 4)  # > cap of 1
            with self.assertRaises(UserError):
                op.action_execute()
            op.action_confirm_blast()
            self.assertTrue(op.blast_confirmed)
            op.action_execute()
            self.assertEqual(op.state, "done")

    def test_internal_sender_guard(self):
        Phish = self.env["bf.reported.phish"]
        external = self.env["res.partner"].create(
            {"name": "Ext", "email": "stranger@outside.test"})
        self.assertFalse(Phish._is_internal_sender(external, external.email))
        employee = self.env["res.users"].create({
            "name": "Emp", "login": "emp@bf.test", "email": "emp@bf.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        self.assertTrue(
            Phish._is_internal_sender(employee.partner_id, "emp@bf.test"))

    # -- audit fixes -----------------------------------------------------
    def test_c1_imap_quote_rejects_control_chars(self):
        cls = type(self.connector)
        for bad in ("x\r\nA1 DELETE", "x\nB", "x\x00y"):
            with self.assertRaises(UserError):
                cls._imap_quote(bad)
        # benign values still quote/escape normally
        self.assertEqual(cls._imap_quote('he"l\\lo'), '"he\\"l\\\\lo"')

    def test_c1_injection_subject_blocks_search(self):
        # A CRLF-laced subject must raise before any IMAP command is sent.
        class FakeSel:
            def select(self, folder, readonly=False):
                return ("OK", [b"1"])
            def logout(self):
                return ("OK", [b"bye"])
        rep = self._report(
            email_from="bad@evil.test",
            subject="x\r\nA1 UID MOVE 1:* Trash", state="threat_confirmed")
        op = self.env["bf.clawback.operation"].browse(
            rep.action_launch_clawback()["res_id"])
        cls = type(self.connector)
        with patch.object(cls, "_mint_token", return_value="tok"), \
                patch.object(cls, "_login", return_value=FakeSel()):
            # real _imap_search -> real _imap_quote -> UserError
            with self.assertRaises(UserError):
                op.action_preview()

    def test_h1_run_move_requires_authorized(self):
        rep = self._report(message_id="<a@evil.test>", state="threat_confirmed")
        op = self.env["bf.clawback.operation"].browse(
            rep.action_launch_clawback()["res_id"])
        op._ensure_hits()
        self.assertFalse(op.authorized)
        with self.assertRaises(UserError):
            op._run("move")  # not authorized -> refuses

    def test_h1_non_purge_cannot_authorize(self):
        manager = self.env["res.users"].create({
            "name": "QA Mgr2", "login": "qa_mgr2", "email": "qa_mgr2@bf.test",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_security_awareness.group_bf_secaware_manager").id])]})
        rep = self._report(message_id="<a@evil.test>", state="threat_confirmed")
        op = self.env["bf.clawback.operation"].browse(
            rep.action_launch_clawback()["res_id"])
        with self.assertRaises(AccessError):
            op.with_user(manager).write({"authorized": True})

    def test_h1_cron_ignores_unauthorized_running(self):
        rep = self._report(message_id="<a@evil.test>", state="threat_confirmed")
        op = self.env["bf.clawback.operation"].browse(
            rep.action_launch_clawback()["res_id"])
        op._ensure_hits()
        op.write({"state": "running"})  # flagged running but NOT authorized
        cls = type(self.connector)
        with patch.object(cls, "_mint_token", return_value="tok"), \
                patch.object(cls, "_login", return_value=MagicMock()), \
                patch.object(cls, "_imap_search", return_value=[b"1"]), \
                patch.object(cls, "_imap_move", return_value=1):
            self.env["bf.clawback.operation"]._cron_process_clawback()
        self.assertEqual(op.messages_removed, 0)
        self.assertFalse(op.authorized)

    def test_m1_blast_cap_applies_to_message_id(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_security_awareness.clawback_max_blast_messages", "1")
        rep = self._report(message_id="<m@evil.test>", state="threat_confirmed")
        op = self.env["bf.clawback.operation"].browse(
            rep.action_launch_clawback()["res_id"])
        cls = type(self.connector)
        with patch.object(cls, "_mint_token", return_value="tok"), \
                patch.object(cls, "_login", return_value=MagicMock()), \
                patch.object(cls, "_imap_search", return_value=[b"1", b"2"]), \
                patch.object(cls, "_imap_move", return_value=2):
            # execute (no preview) must run a search, hit the cap, and refuse
            with self.assertRaises(UserError):
                op.action_execute()
            self.assertFalse(op.authorized)

    def test_m2_delete_mode_is_restorable(self):
        rep = self._report(message_id="<d@evil.test>", state="threat_confirmed")
        op = self.env["bf.clawback.operation"].browse(
            rep.action_launch_clawback()["res_id"])
        op.mode = "delete"
        cls = type(self.connector)
        with patch.object(cls, "_mint_token", return_value="tok"), \
                patch.object(cls, "_login", return_value=MagicMock()), \
                patch.object(cls, "_imap_search", return_value=[b"1", b"2"]), \
                patch.object(cls, "_imap_move", return_value=2):
            op.action_preview()
            op.action_execute()
            self.assertEqual(op.state, "done")
            op.action_restore()  # delete mode is now restorable
            self.assertEqual(op.state, "restored")

    # -- privacy ---------------------------------------------------------
    def test_no_message_body_field(self):
        for model in ("bf.clawback.operation", "bf.clawback.hit"):
            for name in self.env[model]._fields:
                self.assertNotIn(
                    "body", name.lower(),
                    "Clawback must not store email bodies (field %r)." % name)
