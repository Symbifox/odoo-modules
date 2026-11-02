"""The document request engine, under test.

What these checks protect, in order of importance:

1. **The security guard.** The client's page must NEVER receive the
   ``upload_token`` of the underlying transfer. The base's ``/secrets/api/<ut>/
   finalize`` route lets ``recipient_emails`` be rewritten: a client holding
   that token could redirect their own documents to the address of their
   choosing. This is THE test never to remove.

2. **The log chain can be relied on.** It must be intact when nothing has
   moved, and DECLARE ITSELF BROKEN as soon as an entry is tampered with,
   otherwise the access certificate proves nothing.

3. **The shortest retention wins.** A file that holds a one-day document does
   not live seven days because another document is entitled to it.

4. **Cancelling purges, it does not expire.** The base can only expire an
   ACTIVE transfer; ours stays a draft until submission. Going through
   ``action_expire_now()`` would raise: ``_purge_s3()`` is the right call.

Calls to the storage and to VoIP.ms are mocked: these tests exercise the
engine, not the storage provider or the network. The real path was proven by
hand against real storage.

Every expected text is English: the tests pin the English language, whatever
the database's default language is.
"""
import json
from datetime import timedelta
from unittest.mock import patch

import psycopg2

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, HttpCase, TransactionCase, tagged
from odoo.tools import email_split, mute_logger

_SMS = "odoo.addons.bf_securetransfer.models.sms"
_FILE = ("odoo.addons.bf_securetransfer.models.secure_transfer_file"
         ".SecureTransferFile")
_LOGGER = "odoo.addons.bf_document_request.models.bf_document_request"


def _pin_english(cls):
    """Runs the class in English, whatever the database's default language.

    Without a language in the context, a test reads the superuser's language,
    which is French on a French-only database and English on an English-only
    one: an expected text would pass on one and fail on the other. en_US is
    activated when it is not (a French-only database leaves it inactive).
    """
    cls.env["res.lang"]._activate_lang("en_US")
    cls.env = cls.env(context=dict(cls.env.context, lang="en_US"))


# post_install: the base module's emails and pages call into the branding
# modules, which an at_install test would run before they are loaded.
@tagged("post_install", "-at_install")
class BfDocumentCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _pin_english(cls)
        cls.brand = cls.env["secure.transfer.brand"].search([], limit=1)
        # English contact and employee: the client's emails and text
        # messages follow the client's language, the log notes and the
        # transfer the requester's.
        cls.partner = cls.env["res.partner"].create({
            "name": "Test client",
            "phone": "514-555-0199",
            "lang": "en_US",
        })
        cls.employee = cls.env["res.users"].create({
            "name": "Test employee",
            "login": "bf_document_employee_test",
            "email": "employee@example.com",
            "lang": "en_US",
            # An employee is an internal user: without base.group_user they
            # cannot even read their client's contact.
            "groups_id": [
                (4, cls.env.ref("base.group_user").id),
                (4, cls.env.ref(
                    "bf_securetransfer.group_securetransfer_user").id),
            ],
        })
        # ⚠️ Durations the brand OFFERS. The base only accepts one of the
        # EXPIRY_CHOICES durations (1, 7, 30) under the tier's ceiling: 2 and
        # 45 days, the former setup, made 54 tests out of 86 fail when the
        # transfer was created, on any database on the "free" tier (seen in a
        # real deployment). 1 and 7 pass on both tiers, and the shortest one
        # always wins.
        Policy = cls.env["bf.document.expiry.policy"]
        cls.policy_short = Policy.create({
            "name": "Test: 1 day", "retention_days": 1,
        })
        cls.policy_long = Policy.create({
            "name": "Test: 7 days", "retention_days": 7,
        })
        DocType = cls.env["bf.document.type"]
        cls.doc_short = DocType.create({
            "name": "Document kept 1 day", "expiry_policy_id": cls.policy_short.id,
        })
        cls.doc_long = DocType.create({
            "name": "Document kept 7 days", "expiry_policy_id": cls.policy_long.id,
        })
        cls.doc_bare = DocType.create({"name": "Document without policy"})

    # ── factory ──────────────────────────────────────────────────────────────
    def _make_request(self, doc_types=None, required=True, **extra):
        doc_types = doc_types or [self.doc_long]
        if isinstance(required, bool):
            required = [required] * len(doc_types)
        # EXPLICIT channel: the default follows the database (text messages
        # set up or not), and these tests exercise the text message path. The
        # email channel has its own.
        vals = {
            "partner_id": self.partner.id,
            "channel": "sms",
            "phone": "514-555-0199",
            "brand_id": self.brand.id,
            "user_id": self.employee.id,
            "line_ids": [
                (0, 0, {"document_type_id": d.id, "required": r})
                for d, r in zip(doc_types, required)
            ],
        }
        vals.update(extra)
        return self.env["bf.document.request"].create(vals)

    def _send(self, req, delivered=True):
        """Sends with the text message mocked: the engine is tested, not
        VoIP.ms.

        ``with_user(employee)`` is not a detail: ``action_send`` requires the
        "secure transfer" group, and the tests' system user does not have it.
        Sending as the employee is also what real life does, and it is what
        revealed that an employee COULD NOT send.
        """
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=delivered) as sent:
            req.with_user(self.employee).action_send()
        return sent

    def _upload(self, req, line, size=1024, filename="photo.jpg"):
        rec_file = req._register_capture(line, filename, size)
        with patch(_FILE + "._verify_on_s3", return_value=True):
            req._confirm_capture(line)
        return rec_file

    def _submit(self, req):
        with patch(_FILE + "._verify_on_s3", return_value=True):
            return req._submit()


# ═════════════════════════════════════════════ catalogue and retention ══
class TestCatalogueAndRetention(BfDocumentCommon):

    def test_shortest_retention_wins(self):
        req = self._make_request([self.doc_long, self.doc_short])
        self.assertEqual(req._effective_policy(), self.policy_short)

    def test_shortest_retention_is_set_on_the_transfer(self):
        """The computation is useless if it does not reach the transfer:
        without the policy set on it, the base's ``_activate()`` would reapply
        the brand's and the one-day document would live seven days."""
        req = self._make_request([self.doc_long, self.doc_short])
        self._send(req)
        self.assertEqual(req.transfer_id.retention_days, 1)
        self.assertEqual(
            req.transfer_id.bf_document_expiry_policy_id, self.policy_short)

    def test_without_policy_nothing_is_guessed(self):
        """⚠️ The per-type retention is left EMPTY ON PURPOSE: how long a
        document may live is the organisation's decision (per document, per
        role or per step are three different products)."""
        req = self._make_request([self.doc_bare])
        self.assertFalse(req._effective_policy())

    @mute_logger("odoo.sql_db")
    def test_zero_day_policy_is_impossible(self):
        """A zero-day retention would delete the documents the very moment
        the client uploads them. The database forbids it
        (CHECK retention_days > 0)."""
        with self.assertRaises(psycopg2.IntegrityError):
            with self.cr.savepoint():
                self.env["bf.document.expiry.policy"].create({
                    "name": "Test: no duration", "retention_days": 0,
                })

    @mute_logger("odoo.sql_db")
    def test_same_document_twice_is_refused(self):
        with self.assertRaises(psycopg2.IntegrityError):
            with self.cr.savepoint():
                self._make_request([self.doc_long, self.doc_long])


# ═══════════════════════════════════════════════════════════ creation ══
class TestCreation(BfDocumentCommon):

    def test_request_carries_a_sequence_number(self):
        # The prefix is read from the sequence rather than written here: a
        # database upgraded from an earlier release keeps its own numbering
        # (the sequence is noupdate), a fresh install gets "REQ/".
        sequence = self.env.ref("bf_document_request.seq_bf_document_request")
        prefix = (sequence.prefix or "").split("%")[0]
        req = self._make_request()
        self.assertTrue(prefix)
        self.assertTrue(req.name.startswith(prefix), req.name)
        self.assertNotEqual(req.name, "New")

    def test_deleting_a_draft_is_allowed(self):
        req = self._make_request()
        self.assertTrue(req.unlink())

    def test_deleting_a_sent_request_is_refused(self):
        """It left a trace in the chain: deleting it would make the log lie.
        A sent request is cancelled, not deleted."""
        req = self._make_request()
        self._send(req)
        with self.assertRaises(UserError):
            req.unlink()

    def test_phone_is_filled_from_the_client_contact(self):
        req = self.env["bf.document.request"].new({
            "partner_id": self.partner.id,
            "brand_id": self.brand.id,
        })
        req._onchange_partner_id()
        self.assertEqual(req.phone, "514-555-0199")


# ════════════════════════════════════════════════════════════ sending ══
class TestSending(BfDocumentCommon):

    def test_refused_without_document(self):
        req = self.env["bf.document.request"].create({
            "partner_id": self.partner.id,
            "channel": "sms",
            "phone": "514-555-0199",
            "brand_id": self.brand.id,
            "user_id": self.employee.id,
        })
        with self.assertRaises(UserError):
            self._send(req)

    def test_refuses_an_invalid_number(self):
        req = self._make_request()
        req.phone = "hello"
        with self.assertRaises(UserError):
            self._send(req)

    def test_refused_when_text_messages_are_not_set_up(self):
        """No request is created whose client would never receive the link."""
        req = self._make_request()
        with patch(_SMS + ".configured", return_value=False):
            with self.assertRaises(UserError):
                req.with_user(self.employee).action_send()
        self.assertEqual(req.state, "draft")

    def test_refuses_a_requester_without_email(self):
        """The requester is the transfer's RECIPIENT: without an email, the
        documents would have nowhere to go."""
        self.employee.email = False
        req = self._make_request()
        with self.assertRaises(UserError):
            self._send(req)

    def test_refuses_a_user_without_the_group(self):
        outsider = self.env["res.users"].create({
            "name": "Outsider", "login": "bf_document_outsider_test",
            "email": "outsider@example.com", "lang": "en_US",
            "groups_id": [(4, self.env.ref("base.group_user").id)],
        })
        req = self._make_request()
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True):
            with self.assertRaises(UserError):
                req.with_user(outsider).action_send()

    def test_a_regular_employee_can_send(self):
        """⚠️ REGRESSION: ``token`` is restricted to MANAGERS. Without the
        sudo() in action_send, an employee (group_securetransfer_user) got an
        AccessError: the product was only usable as an administrator."""
        req = self._make_request()
        self._send(req)  # goes through with_user(employee)
        self.assertEqual(req.state, "sent")

    def test_mints_a_token_and_a_draft_transfer(self):
        req = self._make_request()
        sent = self._send(req)
        self.assertEqual(req.state, "sent")
        self.assertTrue(req.token, "a request token must be minted")
        self.assertTrue(req.transfer_id)
        self.assertEqual(
            req.transfer_id.state, "draft",
            "the transfer stays a DRAFT until the client submits")
        self.assertTrue(req.sent_at)
        sent.assert_called_once()

    def test_text_message_carries_the_client_link(self):
        req = self._make_request()
        sent = self._send(req)
        body = sent.call_args[0][2]
        self.assertIn(req.token, body)
        self.assertIn("/d/", body)

    def test_text_message_fits_in_one_message(self):
        """The link cannot be shortened: the brand name is what gets trimmed,
        never the URL."""
        self.brand.name = "Organisation with an outrageously long name " * 3
        req = self._make_request()
        body = req._sms_body()
        self.assertLessEqual(len(body), 150)
        self.assertIn(req._public_url(), body)

    def test_sender_code_is_disabled_on_purpose(self):
        """The client is proven by the text message on THEIR number, not by
        an email code they may not have. If this test fails, sending will
        block outright."""
        req = self._make_request()
        self._send(req)
        self.assertTrue(req.transfer_id.sender_confirmed)

    def test_sending_twice_is_refused(self):
        req = self._make_request()
        self._send(req)
        with self.assertRaises(UserError):
            self._send(req)

    def test_undelivered_text_message_leaves_the_request_a_draft(self):
        """⚠️ Raising ROLLS BACK the transaction: the draft transfer, the token
        and the "sent" status go with it. The error message must therefore say
        "stays a draft", not "the request is created, resend it": this test is
        what revealed that the message lied."""
        req = self._make_request()
        with self.assertRaises(UserError):
            self._send(req, delivered=False)
        self.assertEqual(req.state, "draft")
        self.assertFalse(req.token)
        self.assertFalse(req.transfer_id)


# ══════════════════════════════════════════════════════════ reminders ══
class TestReminder(BfDocumentCommon):

    def test_reminder_keeps_the_same_token(self):
        """A client who already has the link must not end up with a dead link
        because they were reminded."""
        req = self._make_request()
        self._send(req)
        token = req.token
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True):
            req.with_user(self.employee).action_resend_sms()
        self.assertEqual(req.token, token)

    def test_reminding_a_draft_is_refused(self):
        req = self._make_request()
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True):
            with self.assertRaises(UserError):
                req.with_user(self.employee).action_resend_sms()

    def test_reminding_a_received_request_is_refused(self):
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        self._submit(req)
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True):
            with self.assertRaises(UserError):
                req.with_user(self.employee).action_resend_sms()

    def test_reminder_log_masks_the_number(self):
        """The hash-chained log ends up on the access certificate: it keeps
        the last four digits, as when sending."""
        req = self._make_request()
        self._send(req)
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True):
            req.with_user(self.employee).action_resend()
        reminder = req.transfer_id.sudo().access_log_ids.filtered(
            lambda entry: entry.action == "bf_document_reminder_sent")
        self.assertEqual(len(reminder), 1)
        self.assertIn("••••••0199", reminder.note)
        self.assertNotIn("514-555-0199", reminder.note)


# ═════════════════════════════════════════════════════════════ upload ══
class TestUpload(BfDocumentCommon):

    def test_an_upload_makes_the_request_partial(self):
        req = self._make_request([self.doc_long, self.doc_short])
        self._send(req)
        self._upload(req, req.line_ids[0])
        self.assertEqual(req.line_ids[0].state, "received")
        self.assertTrue(req.line_ids[0].received_at)
        self.assertEqual(req.state, "partial")

    def test_retaking_the_photo_replaces_the_file(self):
        """Retaking a photo (glare, framing) is the NORMAL case: the first
        file must leave the storage, otherwise each retake eats the quota."""
        req = self._make_request()
        self._send(req)
        line = req.line_ids[0]
        first = self._upload(req, line)
        second = self._upload(req, line)
        self.assertNotEqual(first, second)
        self.assertEqual(line.file_id, second)
        self.assertFalse(first.exists(), "the former file must be removed")

    def test_a_file_too_large_is_refused(self):
        """The mobile page photographs identity documents, it does not upload
        archives: past the threshold, the multipart cycle would be needed,
        which the page does not implement. It refuses cleanly."""
        req = self._make_request()
        self._send(req)
        too_large = 65 * 1024 * 1024
        with self.assertRaises(UserError):
            req._register_capture(req.line_ids[0], "huge.jpg", too_large)

    def test_a_line_of_another_request_is_refused(self):
        req_a = self._make_request()
        req_b = self._make_request([self.doc_short])
        self._send(req_a)
        self._send(req_b)
        with self.assertRaises(UserError):
            req_a._register_capture(req_b.line_ids[0], "photo.jpg", 1024)

    def test_confirming_without_a_file_is_refused(self):
        req = self._make_request()
        self._send(req)
        with self.assertRaises(UserError):
            req._confirm_capture(req.line_ids[0])

    def test_a_file_missing_from_storage_is_refused(self):
        """The browser says its PUT is done: it is not taken at its word, the
        storage is checked."""
        req = self._make_request()
        self._send(req)
        line = req.line_ids[0]
        req._register_capture(line, "photo.jpg", 1024)
        with patch(_FILE + "._verify_on_s3", return_value=False):
            with self.assertRaises(UserError):
                req._confirm_capture(line)
        self.assertEqual(line.state, "pending")

    def test_submitting_is_refused_when_a_required_document_is_missing(self):
        req = self._make_request([self.doc_long, self.doc_short])
        self._send(req)
        self._upload(req, req.line_ids[0])
        with self.assertRaises(UserError):
            self._submit(req)

    def test_submitting_is_refused_when_nothing_was_uploaded(self):
        req = self._make_request([self.doc_long], required=False)
        self._send(req)
        with self.assertRaises(UserError):
            self._submit(req)

    def test_nominal_submission(self):
        """The core: the client submits, the transfer activates, the employee
        receives."""
        req = self._make_request(
            [self.doc_long, self.doc_short], required=[True, False])
        self._send(req)
        self._upload(req, req.line_ids[0])
        self._submit(req)
        self.assertEqual(req.state, "received")
        self.assertTrue(req.completed_at)
        self.assertEqual(
            req.transfer_id.state, "active",
            "this switch is what sends the link to the employee")
        self.assertEqual(req.received_count, 1)

    def test_an_optional_document_not_provided_does_not_hold_the_file(self):
        req = self._make_request(
            [self.doc_long, self.doc_short], required=[True, False])
        self._send(req)
        self._upload(req, req.line_ids[0])
        self._submit(req)
        optional = req.line_ids.filtered(lambda l: not l.required)
        self.assertEqual(optional.state, "skipped")

    def test_no_upload_after_submission(self):
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        self._submit(req)
        with self.assertRaises(UserError):
            req._register_capture(req.line_ids[0], "again.jpg", 1024)


# ═══════════════════════════════════════════════════════════ deadline ══
class TestDeadline(BfDocumentCommon):

    def test_upload_is_refused_after_the_deadline(self):
        req = self._make_request()
        self._send(req)
        req.deadline = fields.Datetime.now() - timedelta(hours=1)
        with self.assertRaises(UserError):
            req._register_capture(req.line_ids[0], "photo.jpg", 1024)

    def test_job_closes_overdue_requests(self):
        req = self._make_request()
        self._send(req)
        req.deadline = fields.Datetime.now() - timedelta(hours=1)
        with patch.object(
            type(req.transfer_id), "_purge_s3", return_value=True
        ) as purge:
            self.env["bf.document.request"]._cron_expire_requests()
        self.assertEqual(req.state, "expired")
        self.assertFalse(req.token, "the link dies")
        purge.assert_called_once()

    def test_job_spares_a_request_without_deadline(self):
        req = self._make_request()
        self._send(req)
        self.assertFalse(req.deadline)
        self.env["bf.document.request"]._cron_expire_requests()
        self.assertEqual(req.state, "sent")
        self.assertTrue(req.token)

    def test_job_spares_a_request_still_in_time(self):
        req = self._make_request()
        self._send(req)
        req.deadline = fields.Datetime.now() + timedelta(days=2)
        self.env["bf.document.request"]._cron_expire_requests()
        self.assertEqual(req.state, "sent")


# ═══════════════════════════════════════════ request without a deadline ══
class TestWithoutDeadline(BfDocumentCommon):
    """A request without a deadline keeps its draft (the base's cleanup spares
    it as long as the request is open). Without an automatic closing, the
    uploaded documents and the client's link lived forever."""

    def _sent_days_ago(self, days):
        req = self._make_request([self.doc_bare])
        self._send(req)
        self.assertFalse(req.deadline)
        req.sudo().sent_at = fields.Datetime.now() - timedelta(days=days)
        return req

    def _cron_expire(self, req):
        with patch.object(type(req.transfer_id), "_purge_s3", return_value=True) as purge:
            self.env["bf.document.request"]._cron_expire_requests()
        return purge

    def test_closed_automatically_after_the_delay(self):
        req = self._sent_days_ago(31)
        purge = self._cron_expire(req)
        self.assertEqual(req.state, "expired")
        self.assertFalse(req.token, "the link dies")
        purge.assert_called_once()  # the draft and its documents are settled
        entry = req.transfer_id.access_log_ids.filtered(
            lambda l: l.action == "bf_document_request_expired")
        self.assertEqual(len(entry), 1)
        self.assertIn("No deadline", entry.note or "")

    def test_open_before_the_delay(self):
        req = self._sent_days_ago(29)
        self._cron_expire(req)
        self.assertEqual(req.state, "sent")
        self.assertTrue(req.token)

    def test_delay_follows_the_parameter_and_zero_disables(self):
        P = self.env["ir.config_parameter"].sudo()
        req = self._sent_days_ago(400)
        P.set_param("bf_document_request.max_open_days", "0")
        self._cron_expire(req)
        self.assertEqual(req.state, "sent", "0: never closed automatically")
        P.set_param("bf_document_request.max_open_days", "500")
        self._cron_expire(req)
        self.assertEqual(req.state, "sent")
        P.set_param("bf_document_request.max_open_days", "365")
        self._cron_expire(req)
        self.assertEqual(req.state, "expired")


# ═══════════════════════════════════════════════════════ cancellation ══
class TestCancellation(BfDocumentCommon):

    def test_cancelling_purges_storage_and_revokes_the_token(self):
        """⚠️ ``_purge_s3()``, NOT ``action_expire_now()``: the base can only
        expire an ACTIVE transfer, and ours stayed a draft."""
        req = self._make_request()
        self._send(req)
        transfer = req.transfer_id
        with patch.object(
            type(transfer), "_purge_s3", return_value=True
        ) as purge:
            req.action_cancel()
        purge.assert_called_once()
        self.assertEqual(req.state, "cancelled")
        self.assertFalse(req.token, "the token must die: the page must close")

    def test_a_regular_employee_can_cancel(self):
        """⚠️ REGRESSION: same cause as for sending: ``token`` is restricted to
        managers, and action_cancel writes it."""
        req = self._make_request()
        self._send(req)
        with patch.object(
            type(req.transfer_id), "_purge_s3", return_value=True
        ):
            req.with_user(self.employee).action_cancel()
        self.assertEqual(req.state, "cancelled")

    def test_cancelling_a_received_request_does_nothing(self):
        """The documents are delivered: they are not taken back from the
        client."""
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        self._submit(req)
        with patch.object(
            type(req.transfer_id), "_purge_s3", return_value=True
        ) as purge:
            req.action_cancel()
        purge.assert_not_called()
        self.assertEqual(req.state, "received")

    def test_unreachable_storage_does_not_prevent_cancelling(self):
        """Best effort: the base's hourly garbage collector will come back."""
        req = self._make_request()
        self._send(req)
        with patch.object(
            type(req.transfer_id), "_purge_s3", side_effect=Exception("storage down")
        ):
            with mute_logger("odoo.addons.bf_document_request.models.bf_document_request"):
                req.action_cancel()
        self.assertEqual(req.state, "cancelled")
        self.assertFalse(req.token)


# ═════════════════════════════════════════════════ log and certificate ══
class TestLogAndCertificate(BfDocumentCommon):

    def test_log_tells_the_whole_lifecycle(self):
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        self._submit(req)
        actions = req.transfer_id.access_log_ids.mapped("action")
        for expected in ("bf_document_request_sent", "bf_document_doc_received",
                         "bf_document_request_completed"):
            self.assertIn(expected, actions)

    def test_one_chain_not_two(self):
        """The request's lifecycle lives in THE transfer's chain: one digest
        to show the client, not two logs to reconcile."""
        req = self._make_request()
        self._send(req)
        self.assertTrue(req.transfer_id.access_log_ids)
        self.assertFalse(
            hasattr(req, "access_log_ids"),
            "the request must not keep its own log")

    def test_chain_is_intact(self):
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        ctx = req.transfer_id._certificate_context()
        self.assertTrue(ctx["intact"])
        self.assertTrue(ctx["rows"])
        self.assertEqual(ctx["rows"][0]["seq"], 1)

    def test_a_tampered_entry_breaks_the_chain(self):
        """Without this test, the certificate proves NOTHING: the chain must
        declare itself broken when it is touched. The tampering goes through
        SQL, because the ORM precisely forbids writing to the log."""
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        entry = req.transfer_id.access_log_ids[0]
        self.assertTrue(req.transfer_id._certificate_context()["intact"])
        self.env.cr.execute(
            "UPDATE secure_transfer_access_log SET ip = %s WHERE id = %s",
            ("6.6.6.6", entry.id),
        )
        req.transfer_id.access_log_ids.invalidate_recordset()
        self.assertFalse(
            req.transfer_id._certificate_context()["intact"],
            "a rewritten IP must break the SHA-256 chain")

    def test_log_is_append_only(self):
        req = self._make_request()
        self._send(req)
        entry = req.transfer_id.access_log_ids[0]
        with self.assertRaises(UserError):
            entry.ip = "6.6.6.6"
        with self.assertRaises(UserError):
            entry.unlink()

    def test_certificate_really_renders_as_pdf(self):
        """``force_report_rendering``: without it, Odoo bypasses wkhtmltopdf
        in test mode and renders HTML; the test would pass without ever
        producing a single PDF."""
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        report = self.env["ir.actions.report"].with_context(
            force_report_rendering=True)
        pdf, ftype = report._render_qweb_pdf(
            "bf_securetransfer.report_access_certificate",
            res_ids=req.transfer_id.ids,
        )
        self.assertEqual(ftype, "pdf")
        self.assertTrue(pdf.startswith(b"%PDF-"))

    def test_certificate_carries_the_verdict_and_the_recipe(self):
        """A certificate without its verification recipe proves nothing: it
        asks for trust. The HTML must carry the verdict, the end-of-chain
        digests, and what is needed to recompute them."""
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        html = self.env["ir.actions.report"]._render_qweb_html(
            "bf_securetransfer.report_access_certificate",
            req.transfer_id.ids,
        )[0].decode()
        # "INTACT" also matches the French "INTACTE": the certificate is
        # printed in the transfer's language, not in the test's.
        self.assertIn("INTACT", html)
        self.assertIn("SHA-256", html)
        self.assertIn("UTC", html)
        self.assertIn(req.transfer_id.name, html)
        self.assertNotIn(
            req.transfer_id.upload_token, html,
            "a certificate must not carry a capability around")

    def test_button_renders_the_report_not_the_layout_wizard(self):
        """⚠️ Odoo trap: report_action() diverts printing to the "configure
        your document layout" wizard when the company has no
        external_report_layout_id AND the user is an administrator. Hence
        config=False: this certificate carries the brand of the organisation
        that requested the documents, not the company's layout."""
        self.env.company.external_report_layout_id = False
        req = self._make_request()
        self._send(req)
        action = req.action_print_certificate()
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertEqual(
            action["report_name"], "bf_securetransfer.report_access_certificate")

    def test_no_certificate_before_sending(self):
        req = self._make_request()
        with self.assertRaises(UserError):
            req.action_print_certificate()


# ═════════════════════════════════════════════════════════════ portal ══
@tagged("post_install", "-at_install")
class TestPortal(BfDocumentCommon, HttpCase):

    # The public pages follow Accept-Language, not the test's context: without
    # the header, the base serves its default language.
    HEADERS = {"Accept-Language": "en"}

    def _page(self, path):
        return self.url_open(path, headers=self.HEADERS)

    def _rpc(self, path, **params):
        payload = {"jsonrpc": "2.0", "method": "call", "params": params}
        response = self.url_open(
            path, data=json.dumps(payload),
            headers=dict(self.HEADERS, **{"Content-Type": "application/json"}),
        )
        return response.json().get("result") or {}

    def test_page_never_leaks_the_upload_token(self):
        """THE GUARD NEVER TO REMOVE.

        ``/secrets/api/<upload_token>/finalize`` lets ``recipient_emails`` be
        rewritten: a client holding that token could redirect their own
        documents to the address of their choosing. The upload page therefore
        receives ONLY the request token, which only opens an upload.
        """
        req = self._make_request()
        self._send(req)
        self.env.cr.flush()
        upload_token = req.transfer_id.upload_token
        self.assertTrue(upload_token, "the transfer must have a token")

        page = self._page("/d/%s" % req.token)
        self.assertEqual(page.status_code, 200)
        body = page.text
        self.assertIn(req.token, body, "the request token is expected")
        self.assertNotIn(
            upload_token, body,
            "LEAK: the base's upload token is in the client's page; they "
            "could redirect their own documents.")

    def test_page_opens_and_logs_the_visit(self):
        req = self._make_request()
        self._send(req)
        self.env.cr.flush()
        page = self._page("/d/%s" % req.token)
        self.assertEqual(page.status_code, 200)
        self.assertIn(self.doc_long.name, page.text)
        self.assertIn(
            "bf_document_request_opened",
            req.transfer_id.access_log_ids.mapped("action"))

    def test_a_fake_token_closes_the_page_without_crashing(self):
        page = self._page("/d/00000000000000000000000000000000")
        self.assertEqual(page.status_code, 200, "no 500 on a fake token")
        self.assertNotIn(self.doc_long.name, page.text)

    def test_a_revoked_token_closes_the_page(self):
        req = self._make_request()
        self._send(req)
        token = req.token
        with patch.object(type(req.transfer_id), "_purge_s3", return_value=True):
            req.action_cancel()
        self.env.cr.flush()
        page = self._page("/d/%s" % token)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(self.doc_long.name, page.text)

    def test_a_received_document_offers_another_file(self):
        """The button replaces the received document, like "Retake": it says
        so."""
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        self.env.cr.flush()
        page = self._page("/d/%s" % req.sudo().token)
        self.assertIn("Choose another file", page.text)
        from odoo.tools import file_open
        with file_open("bf_document_request/static/src/js/"
                       "bf_document_request.js") as script:
            self.assertIn("S.choose_again", script.read())

    def test_presigning_with_a_fake_token_is_refused(self):
        res = self._rpc("/d/00000000000000000000000000000000/presign",
                        line_id=1, size=1024, filename="photo.jpg")
        self.assertTrue(res.get("error"), res)
        self.assertNotIn("url", res)

    def test_presigning_a_line_of_another_request_is_refused(self):
        """The token opens ONE file only: it must not serve to upload into
        the neighbour's."""
        req_a = self._make_request()
        req_b = self._make_request([self.doc_short])
        self._send(req_a)
        self._send(req_b)
        self.env.cr.flush()
        res = self._rpc("/d/%s/presign" % req_a.token,
                        line_id=req_b.line_ids[0].id, size=1024,
                        filename="photo.jpg")
        self.assertEqual(res.get("error"), "invalid_line", res)

    def test_presigning_an_empty_file_is_refused(self):
        req = self._make_request()
        self._send(req)
        self.env.cr.flush()
        res = self._rpc("/d/%s/presign" % req.token,
                        line_id=req.line_ids[0].id, size=0,
                        filename="empty.jpg")
        self.assertEqual(res.get("error"), "invalid_size", res)

    def test_client_journey_through_the_page(self):
        """Presign, confirmation, photo retake and submission, through the
        public routes: the documents are always written, even though their
        fields are only written under sudo."""
        req = self._make_request(
            [self.doc_long, self.doc_short], required=[True, False])
        self._send(req)
        self.env.cr.flush()
        token = req.sudo().token
        line = req.line_ids[0]
        plan = {"url": "https://storage.example/put", "headers": {}}
        with patch(_FILE + ".presign_simple", return_value=plan), \
             patch(_FILE + "._verify_on_s3", return_value=True), \
             patch(_FILE + "._s3_delete"):
            first = self._rpc("/d/%s/presign" % token, line_id=line.id,
                              size=1024, filename="front.jpg")
            self.assertEqual(first.get("url"), plan["url"], first)
            self.assertEqual(
                self._rpc("/d/%s/uploaded" % token, line_id=line.id),
                {"ok": True, "state": "received"})
            second = self._rpc("/d/%s/presign" % token, line_id=line.id,
                               size=2048, filename="front-2.jpg")
            self.assertTrue(second.get("file_id"), second)
            self.assertNotEqual(second["file_id"], first["file_id"])
            self._rpc("/d/%s/uploaded" % token, line_id=line.id)
            self.assertEqual(self._rpc("/d/%s/submit" % token), {"ok": True})
        self.env.invalidate_all()
        self.assertEqual(req.state, "received")
        self.assertEqual(line.file_id.id, second["file_id"])
        self.assertFalse(self.env["secure.transfer.file"].browse(
            first["file_id"]).exists(), "the retake removes the first file")
        self.assertEqual(req.line_ids[1].state, "skipped")

    def test_page_does_not_delete_another_transfer_file(self):
        """A line that would point to another transfer's file does not get
        that file deleted by a photo retake."""
        other = self._make_request([self.doc_short])
        self._send(other)
        rec_file = self._upload(other, other.line_ids)
        req = self._make_request()
        self._send(req)
        req.line_ids.sudo().write({"file_id": rec_file.id})
        self.env.cr.flush()
        with patch(_FILE + "._s3_delete") as deletion:
            res = self._rpc("/d/%s/presign" % req.sudo().token,
                            line_id=req.line_ids.id, size=1024,
                            filename="photo.jpg")
        self.assertEqual(res.get("error"), "invalid", res)
        deletion.assert_not_called()
        self.env.invalidate_all()
        self.assertTrue(rec_file.exists())
        self.assertEqual(other.line_ids.file_id, rec_file)


# ═══════════════════════════════════════════ automatic reminders (job) ══
class TestAutomaticReminder(BfDocumentCommon):
    """The automatic reminder, and above all its brakes.

    The first sending was never the problem: the file still incomplete three
    days later is. But a badly bounded automatic reminder is worse than no
    reminder at all, for two reasons that each have their test here:

    1. **The carrier's quota is shared.** VoIP.ms blocks at around 27
       messages in a burst. Uncapped reminders can eat the quota and make a
       FIRST sending fail: a client who never receives their request.
    2. **The client really receives the text messages.** Hence the budget per
       request, the minimum delay, and the refusal to remind a file that
       expires within the hour.
    """

    def setUp(self):
        super().setUp()
        # ⚠️ A DEDICATED brand, and it is essential. The job sweeps the whole
        # database, and the database may hold real open requests that share
        # the default brand: without this isolation, each test counts the
        # text messages of other files and the assertions become chance.
        # That is exactly what made 7 tests fail on the first run.
        self.brand = self.env["secure.transfer.brand"].create({
            "name": "Test brand (reminders)",
            "company_id": self.env.company.id,
            "slug": "test-auto-reminder",
            "bf_document_reminder_enabled": True,
            "bf_document_reminder_delay_hours": 24,
            "bf_document_reminder_max": 2,
        })

    # ── tools ───────────────────────────────────────────────────────────────
    def _age(self, req, hours):
        """Moves the last contact back to simulate the wait."""
        past = fields.Datetime.now() - timedelta(hours=hours)
        req.sudo().write({"sent_at": past, "last_reminder_at": False})

    def _cron(self, delivered=True):
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=delivered) as sent:
            self.env["bf.document.request"]._cron_send_reminders()
        return sent

    # ── the nominal case ────────────────────────────────────────────────────
    def test_job_reminds_an_incomplete_file(self):
        req = self._make_request()
        self._send(req)
        self._age(req, 25)
        sent = self._cron()
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(req.reminder_count, 1)
        self.assertTrue(req.last_reminder_at)

    def test_job_waits_for_the_delay(self):
        """Reminding someone two hours after writing to them is harassment,
        not a reminder."""
        req = self._make_request()
        self._send(req)
        self._age(req, 2)
        sent = self._cron()
        self.assertEqual(sent.call_count, 0)
        self.assertEqual(req.reminder_count, 0)

    def test_reminder_budget_is_respected(self):
        req = self._make_request()
        self._send(req)
        for expected in (1, 2):
            self._age(req, 25)
            req.sudo().reminder_count = expected - 1
            self._cron()
            self.assertEqual(req.reminder_count, expected)
        # The budget is used up: the third run must send nothing.
        self._age(req, 25)
        req.sudo().reminder_count = 2
        sent = self._cron()
        self.assertEqual(sent.call_count, 0)
        self.assertEqual(req.reminder_count, 2)

    # ── the brakes ──────────────────────────────────────────────────────────
    def test_no_reminder_when_the_brand_does_not_want_it(self):
        """Sending automatic text messages to clients without anyone asking
        for it is the kind of default nobody forgives."""
        self.brand.bf_document_reminder_enabled = False
        req = self._make_request()
        self._send(req)
        self._age(req, 25)
        sent = self._cron()
        self.assertEqual(sent.call_count, 0)

    def test_no_reminder_on_an_overdue_file(self):
        """The expiry job is about to close it: reminding the client would
        only lead them to a link that dies."""
        req = self._make_request()
        self._send(req)
        self._age(req, 25)
        req.sudo().deadline = fields.Datetime.now() - timedelta(minutes=5)
        sent = self._cron()
        self.assertEqual(sent.call_count, 0)
        self.assertEqual(req.reminder_count, 0)

    def test_no_reminder_on_a_complete_file(self):
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        self._submit(req)
        self._age(req, 25)
        sent = self._cron()
        self.assertEqual(sent.call_count, 0)

    def test_daily_cap_bounds_the_run(self):
        """The brake that protects FIRST sendings: without it, reminders can
        eat the carrier's quota."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_document_request.reminder_daily_cap", "2")
        requests = []
        for _i in range(4):
            req = self._make_request()
            self._send(req)
            self._age(req, 25)
            requests.append(req)
        sent = self._cron()
        self.assertEqual(sent.call_count, 2)
        self.assertEqual(
            sum(1 for r in requests if r.reminder_count), 2,
            "the cap must bound the run, not shift it")

    def test_a_zero_cap_disables_reminders(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_document_request.reminder_daily_cap", "0")
        req = self._make_request()
        self._send(req)
        self._age(req, 25)
        sent = self._cron()
        self.assertEqual(sent.call_count, 0)

    @mute_logger("odoo.addons.bf_document_request.models.bf_document_request")
    def test_a_text_message_failure_does_not_use_up_the_budget(self):
        """``sms.send`` never raises, it returns False. A failure is most often
        an exhausted quota: the request must keep its budget intact to be
        reminded on the next run."""
        req = self._make_request()
        self._send(req)
        self._age(req, 25)
        self._cron(delivered=False)
        self.assertEqual(req.reminder_count, 0)
        self.assertFalse(req.last_reminder_at)
        # The next run, once the carrier is back, must go through.
        sent = self._cron(delivered=True)
        self.assertEqual(sent.call_count, 1)
        self.assertEqual(req.reminder_count, 1)

    # ── the content, and the manual / automatic boundary ────────────────────
    def test_reminder_only_counts_what_is_missing(self):
        """Asking again for "2 documents" from someone who already uploaded
        one is the best way to make them give up."""
        req = self._make_request([self.doc_long, self.doc_short])
        self._send(req)
        self._upload(req, req.line_ids[0])
        body = req.sudo()._sms_reminder_body()
        self.assertIn("1 document", body)
        self.assertIn("Reminder", body)

    def test_all_uploaded_the_reminder_asks_to_confirm(self):
        """Everything is uploaded, nothing is submitted: "0 document(s) to
        upload" would let the client believe they were done."""
        req = self._make_request()
        self._send(req)
        self._upload(req, req.line_ids[0])
        body = req.sudo()._sms_reminder_body()
        self.assertNotIn("0 document", body)
        self.assertIn("please confirm sending", body)
        self.assertIn(req.sudo()._public_url(), body)
        self.assertLessEqual(len(body), 150)

    def test_reminder_fits_in_one_text_message(self):
        req = self._make_request()
        self._send(req)
        self.brand.name = "Organisation with an endless name" * 4
        self.assertLessEqual(len(req.sudo()._sms_reminder_body()), 150)

    def test_manual_reminder_does_not_use_up_the_automatic_budget(self):
        """The budget bounds the machine, not the employee."""
        req = self._make_request()
        self._send(req)
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True):
            req.with_user(self.employee).action_resend_sms()
        self.assertEqual(req.reminder_count, 0)
        self.assertTrue(req.last_reminder_at,
                        "it must still be timestamped, otherwise the job "
                        "doubles up on the employee within the minute")

    def test_manual_reminder_pushes_the_job_back(self):
        req = self._make_request()
        self._send(req)
        self._age(req, 25)
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True):
            req.with_user(self.employee).action_resend_sms()
        sent = self._cron()
        self.assertEqual(sent.call_count, 0,
                         "the client just received a text message from the "
                         "employee")


# ═══════════════════════════════════════════════════════ draft cleanup ══
class TestDraftCleanup(BfDocumentCommon):
    """The draft transfer of an open request waits for the documents until
    the REQUEST's deadline. The base's hourly cleanup (24 h by default) used to
    cancel it, photos included, and the request stayed "Sent"."""

    def _age(self, transfer, hours=72):
        self.env.cr.execute(
            "UPDATE secure_transfer SET create_date = now() - %s * interval '1 hour' WHERE id = %s",
            (hours, transfer.id))
        transfer.invalidate_recordset(["create_date"])

    def _collectable(self):
        T = self.env["secure.transfer"].sudo()
        cutoff = fields.Datetime.now() - timedelta(hours=24)
        return T.search(T._gc_drafts_domain(cutoff))

    def test_draft_of_an_open_request_is_spared(self):
        req = self._make_request([self.doc_bare])
        self._send(req)
        self.assertEqual(req.state, "sent")
        self._age(req.transfer_id)
        self.assertNotIn(req.transfer_id, self._collectable())

    def test_draft_becomes_collectable_again_once_the_request_is_closed(self):
        req = self._make_request([self.doc_bare])
        self._send(req)
        self._age(req.transfer_id)
        req.sudo().state = "expired"
        self.assertIn(req.transfer_id, self._collectable())


# ═══════════════════════════════════════════════════════ email channel ══
class BfDocumentEmailCommon(BfDocumentCommon):
    """The link goes out by email. What these tests protect:

    1. **The link stays nowhere an employee could read it.** The token is
       restricted to managers; an email posted on the request's chatter, or
       authored by the employee, would make it readable to them.
    2. **A refused request leaves nothing behind**: no draft transfer, no
       token, no queued email.
    3. **Text messages turned off no longer block anything**: that was the
       state of a real deployment, where every request stayed a draft.
    """

    ADDRESS = "client.email@example.com"

    def _make_mail_request(self, doc_types=None, **extra):
        vals = {"channel": "email", "email": self.ADDRESS, "phone": False}
        vals.update(extra)
        return self._make_request(doc_types, **vals)

    def _mails(self, address=None):
        return self.env["mail.mail"].sudo().search(
            [("email_to", "ilike", address or self.ADDRESS)], order="id asc")

    def _send_mail(self, req):
        """Sends by email, with text messages declared OFF: the email channel
        must not ask anything of them."""
        with patch(_SMS + ".configured", return_value=False), \
             patch(_SMS + ".send", return_value=True) as sms_send:
            req.with_user(self.employee).action_send()
        sms_send.assert_not_called()


class TestChannel(BfDocumentEmailCommon):

    def test_default_is_text_message_when_set_up(self):
        with patch(_SMS + ".configured", return_value=True):
            req = self.env["bf.document.request"].create({
                "partner_id": self.partner.id, "brand_id": self.brand.id,
                "user_id": self.employee.id,
            })
        self.assertEqual(req.channel, "sms")

    def test_default_is_email_without_text_messages(self):
        with patch(_SMS + ".configured", return_value=False):
            req = self.env["bf.document.request"].create({
                "partner_id": self.partner.id, "brand_id": self.brand.id,
                "user_id": self.employee.id,
            })
        self.assertEqual(req.channel, "email")

    def test_email_is_filled_from_the_client_contact(self):
        self.partner.email = "contact@example.com"
        req = self.env["bf.document.request"].new({
            "partner_id": self.partner.id,
            "brand_id": self.brand.id,
        })
        req._onchange_partner_id()
        self.assertEqual(req.email, "contact@example.com")

    def test_changing_client_rewrites_address_and_mobile(self):
        """Keeping the first client's details would send them the link to
        the second client's file."""
        self.partner.email = "contact@example.com"
        other = self.env["res.partner"].create({
            "name": "Other client", "email": "other@example.com",
            "mobile": "438-555-0123", "lang": "en_US",
        })
        req = self.env["bf.document.request"].new({
            "partner_id": self.partner.id,
            "brand_id": self.brand.id,
        })
        req._onchange_partner_id()
        self.assertEqual(req.email, "contact@example.com")
        self.assertEqual(req.phone, "514-555-0199")
        req.partner_id = other
        req._onchange_partner_id()
        self.assertEqual(req.email, "other@example.com")
        self.assertEqual(req.phone, "438-555-0123")

    def test_a_client_without_details_clears_the_previous_ones(self):
        bare = self.env["res.partner"].create({
            "name": "Client without details", "lang": "en_US"})
        req = self.env["bf.document.request"].new({
            "partner_id": self.partner.id,
            "brand_id": self.brand.id,
            "email": "former@example.com",
            "phone": "514-555-0199",
        })
        req.partner_id = bare
        req._onchange_partner_id()
        self.assertFalse(req.email)
        self.assertFalse(req.phone)


class TestEmailSending(BfDocumentEmailCommon):

    def test_nominal_sending_without_text_messages(self):
        req = self._make_mail_request()
        self._send_mail(req)
        self.assertEqual(req.state, "sent")
        self.assertTrue(req.sudo().token)
        self.assertEqual(req.transfer_id.state, "draft")
        self.assertEqual(len(self._mails()), 1)

    def test_email_carries_the_client_link(self):
        req = self._make_mail_request()
        self._send_mail(req)
        mail = self._mails()
        self.assertIn(req.sudo()._public_url(), mail.body_html)
        self.assertIn("/d/", mail.body_html)

    def test_email_comes_from_the_brand_and_replies_to_the_requester(self):
        # A fresh database has an address neither for the brand nor for the
        # company: the sender would fall back on the current user, who changes
        # between the sending and the comparison.
        self.env.company.email = "office@example.com"
        req = self._make_mail_request()
        self._send_mail(req)
        mail = self._mails()
        self.assertEqual(mail.email_from, req.transfer_id._brand_email_from())
        self.assertNotIn("employee@example.com", mail.email_from)
        self.assertIn("employee@example.com", mail.reply_to or "")
        self.assertIn(self.ADDRESS, mail.email_to)
        self.assertTrue(mail.auto_delete,
                        "the link must not outlive the sending in the queue")

    def test_email_is_attached_to_no_chatter(self):
        """The heart of the channel's security: nothing that carries the
        token may land on the chatter of the request or of the transfer."""
        req = self._make_mail_request()
        self._send_mail(req)
        mail = self._mails()
        self.assertFalse(mail.model)
        self.assertFalse(mail.res_id)
        self.assertFalse(mail.author_id,
                         "an employee authoring the message could read it again")
        token = req.sudo().token
        traces = self.env["mail.message"].sudo().search([
            ("model", "in", ("bf.document.request", "secure.transfer")),
            ("res_id", "in", (req.id, req.transfer_id.id)),
        ])
        self.assertTrue(traces, "the chatter must still say it was sent")
        for msg in traces:
            self.assertNotIn(token, msg.body or "")

    def test_the_employee_cannot_read_the_link_again(self):
        """``token`` is restricted to managers: the email's message must not
        become a side path to it."""
        req = self._make_mail_request()
        self._send_mail(req)
        token = req.sudo().token
        mail = self._mails()
        seen = self.env["mail.message"].with_user(self.employee).search(
            [("body", "ilike", token)])
        self.assertFalse(seen)
        # DIRECT read, by id: the search filters, but Odoo lets a message's
        # author and its CREATOR read it.
        self.assertNotEqual(mail.mail_message_id.create_uid, self.employee)
        with self.assertRaises(AccessError):
            mail.mail_message_id.with_user(self.employee).read(["body"])

    def test_chatter_states_the_address(self):
        req = self._make_mail_request()
        self._send_mail(req)
        body = " ".join(req.message_ids.mapped("body"))
        self.assertIn(self.ADDRESS, body)

    def test_transfer_sender_is_the_client_address(self):
        req = self._make_mail_request()
        self._send_mail(req)
        self.assertEqual(req.transfer_id.sender_email, self.ADDRESS)
        self.assertTrue(req.transfer_id.sender_confirmed)

    def test_log_does_not_copy_the_whole_address(self):
        req = self._make_mail_request()
        self._send_mail(req)
        notes = " ".join(req.transfer_id.sudo().access_log_ids.mapped("note"))
        self.assertIn("c•••@example.com", notes)
        self.assertNotIn(self.ADDRESS, notes)

    def test_email_escapes_user_input(self):
        self.partner.name = "<b>Client</b>"
        req = self._make_mail_request(
            message="<script>alert(1)</script>\nSecond line")
        self.doc_long.name = "Document <img src=x onerror=alert(1)>"
        self._send_mail(req)
        body = self._mails().body_html
        self.assertNotIn("<script>", body)
        self.assertNotIn("<img src=x", body)
        self.assertNotIn("<b>Client</b>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("Second line", body)

    def test_a_line_break_in_the_name_adds_no_recipient(self):
        """"Client<CR><LF>other@..." made ``email_split`` read two addresses,
        hence two recipients of the link."""
        self.partner.name = "Client\r\nother@pirate.example"
        req = self._make_mail_request()
        self._send_mail(req)
        self.assertEqual(email_split(self._mails().email_to), [self.ADDRESS])

    def test_reply_only_goes_to_the_requester(self):
        """Same trap in ``reply_to``: the requester's name is stripped of
        control characters. ASCII name on purpose: an accented name goes out
        base64-encoded and does not carry the trap."""
        self.employee.name = "Employee\r\nBcc: spy@pirate.example"
        req = self._make_mail_request()
        self._send_mail(req)
        reply_to = self._mails().reply_to
        self.assertEqual(email_split(reply_to), ["employee@example.com"])
        self.assertNotIn("\n", reply_to)
        self.assertNotIn("\r", reply_to)

    def test_email_states_the_deadline(self):
        req = self._make_mail_request(
            deadline=fields.Datetime.now() + timedelta(days=3))
        self._send_mail(req)
        self.assertIn("Please upload them before", self._mails().body_html)

    def test_refuses_an_invalid_address_leaving_nothing(self):
        for address in ("hello", "", "a@example.com, b@example.com"):
            req = self._make_mail_request(email=address)
            with self.assertRaises(UserError):
                self._send_mail(req)
            self.assertEqual(req.state, "draft")
            self.assertFalse(req.sudo().token)
            self.assertFalse(req.transfer_id)
        self.assertFalse(self.env["mail.mail"].sudo().search(
            [("email_to", "ilike", "example.com"),
             ("body_html", "ilike", "/d/")]))

    def test_email_channel_ignores_the_mobile(self):
        req = self._make_mail_request(phone="hello")
        self._send_mail(req)
        self.assertEqual(req.state, "sent")

    def test_text_messages_off_still_block_the_text_message_channel(self):
        req = self._make_request()
        with patch(_SMS + ".configured", return_value=False):
            with self.assertRaises(UserError):
                req.with_user(self.employee).action_send()
        self.assertEqual(req.state, "draft")

    def test_sending_twice_is_refused(self):
        req = self._make_mail_request()
        self._send_mail(req)
        with self.assertRaises(UserError):
            self._send_mail(req)
        self.assertEqual(len(self._mails()), 1)


class TestEmailReminder(BfDocumentEmailCommon):

    def test_reminder_keeps_the_same_token(self):
        req = self._make_mail_request()
        self._send_mail(req)
        token = req.sudo().token
        req.with_user(self.employee).action_resend()
        self.assertEqual(req.sudo().token, token)
        mails = self._mails()
        self.assertEqual(len(mails), 2)
        self.assertIn(req.sudo()._public_url(), mails[-1].body_html)
        self.assertIn("Reminder", mails[-1].subject)

    def test_manual_reminder_is_timestamped_without_using_the_budget(self):
        req = self._make_mail_request()
        self._send_mail(req)
        req.with_user(self.employee).action_resend()
        self.assertTrue(req.last_reminder_at)
        self.assertEqual(req.reminder_count, 0)

    def test_reminder_only_lists_what_is_missing(self):
        req = self._make_mail_request([self.doc_long, self.doc_bare])
        self._send_mail(req)
        self._upload(req, req.line_ids[0])
        req.with_user(self.employee).action_resend()
        mail = self._mails()[-1]
        self.assertNotIn(self.doc_long.name, mail.body_html)
        self.assertIn(self.doc_bare.name, mail.body_html)
        self.assertIn("1 document", mail.subject)

    def test_all_uploaded_the_reminder_asks_to_confirm(self):
        """Every document is uploaded, none is submitted. The email does not
        say "0 document(s)", it says what to do."""
        req = self._make_mail_request()
        self._send_mail(req)
        self._upload(req, req.line_ids[0])
        req.with_user(self.employee).action_resend()
        mail = self._mails()[-1]
        self.assertNotIn("0 document", mail.subject)
        self.assertIn("please confirm sending", mail.subject)
        self.assertIn("Finish and send", mail.body_html)
        self.assertNotIn(self.doc_long.name, mail.body_html)
        self.assertIn(req.sudo()._public_url(), mail.body_html)

    def test_reminding_a_draft_is_refused(self):
        req = self._make_mail_request()
        with self.assertRaises(UserError):
            req.with_user(self.employee).action_resend()
        self.assertFalse(self._mails())

    def test_former_text_message_button_refuses_an_email_request(self):
        req = self._make_mail_request()
        self._send_mail(req)
        with patch(_SMS + ".send", return_value=True) as sms_send:
            with self.assertRaises(UserError):
                req.with_user(self.employee).action_resend_sms()
        sms_send.assert_not_called()

    def test_resending_by_text_message_still_uses_text_messages(self):
        req = self._make_request()
        self._send(req)
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True) as sms_send:
            req.with_user(self.employee).action_resend()
        sms_send.assert_called_once()
        self.assertFalse(self.env["mail.mail"].sudo().search(
            [("body_html", "ilike", req.sudo()._public_url())]))


class TestAutomaticEmailReminder(BfDocumentEmailCommon):

    def setUp(self):
        super().setUp()
        # Dedicated brand: the job sweeps the whole database (see
        # TestAutomaticReminder, which paid for this trap).
        self.brand = self.env["secure.transfer.brand"].create({
            "name": "Test brand (email reminders)",
            "company_id": self.env.company.id,
            "slug": "test-email-reminder",
            "bf_document_reminder_enabled": True,
            "bf_document_reminder_delay_hours": 24,
            "bf_document_reminder_max": 2,
        })

    def _age(self, req, hours=25):
        past = fields.Datetime.now() - timedelta(hours=hours)
        req.sudo().write({"sent_at": past, "last_reminder_at": False})

    def _cron(self, delivered=True):
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=delivered) as sms_send:
            self.env["bf.document.request"]._cron_send_reminders()
        return sms_send

    def test_job_reminds_by_email(self):
        req = self._make_mail_request()
        self._send_mail(req)
        self._age(req)
        sms_send = self._cron()
        sms_send.assert_not_called()
        self.assertEqual(req.reminder_count, 1)
        self.assertEqual(len(self._mails()), 2)

    def test_job_waits_for_the_delay(self):
        req = self._make_mail_request()
        self._send_mail(req)
        self._age(req, 2)
        self._cron()
        self.assertEqual(req.reminder_count, 0)
        self.assertEqual(len(self._mails()), 1)

    def test_after_an_address_change_the_automatic_reminder_carries_the_new_link(self):
        """This is why the chatter no longer promises that nothing will go out
        before "Resend"."""
        req = self._make_mail_request()
        self._send_mail(req)
        req.with_user(self.employee).write({"email": "new@example.com"})
        new_token = req.sudo().token
        self._age(req)
        self._cron()
        self.assertEqual(req.reminder_count, 1)
        self.assertIn(new_token, self._mails("new@example.com").body_html)

    def test_per_request_budget_also_applies_to_email(self):
        req = self._make_mail_request()
        self._send_mail(req)
        self._age(req)
        req.sudo().reminder_count = 2
        self._cron()
        self.assertEqual(req.reminder_count, 2)
        self.assertEqual(len(self._mails()), 1)

    def test_text_message_cap_does_not_block_email(self):
        """The cap protects the VoIP.ms quota; an email takes nothing from it."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_document_request.reminder_daily_cap", "0")
        req = self._make_mail_request()
        self._send_mail(req)
        self._age(req)
        self._cron()
        self.assertEqual(req.reminder_count, 1)

    def test_a_text_message_failure_does_not_stop_the_emails(self):
        sms_req = self._make_request()
        self._send(sms_req)
        mail_req = self._make_mail_request()
        self._send_mail(mail_req)
        # The text message request comes first in the job's order.
        self._age(mail_req, 25)
        self._age(sms_req, 26)
        with mute_logger(
                "odoo.addons.bf_document_request.models.bf_document_request"):
            self._cron(delivered=False)
        self.assertEqual(sms_req.reminder_count, 0)
        self.assertEqual(mail_req.reminder_count, 1)

    def test_a_reached_text_message_cap_lets_the_next_emails_through(self):
        """The day's cap, already used up by a manual text message reminder,
        holds back the text message request that comes first; it must not stop
        the run for all that: the email request that follows is reminded."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_document_request.reminder_daily_cap", "1")
        today = self._make_request()
        self._send(today)
        with patch(_SMS + ".configured", return_value=True), \
             patch(_SMS + ".send", return_value=True):
            today.with_user(self.employee).action_resend_sms()
        sms_req = self._make_request()
        self._send(sms_req)
        mail_req = self._make_mail_request()
        self._send_mail(mail_req)
        # The text message request comes before the email request in the
        # job's order.
        self._age(sms_req, 26)
        self._age(mail_req, 25)
        sms_send = self._cron()
        sms_send.assert_not_called()
        self.assertEqual(sms_req.reminder_count, 0)
        self.assertEqual(mail_req.reminder_count, 1)
        self.assertEqual(len(self._mails()), 2)

    def test_email_reminders_do_not_use_up_the_text_message_cap(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_document_request.reminder_daily_cap", "1")
        mail_req = self._make_mail_request()
        self._send_mail(mail_req)
        mail_req.with_user(self.employee).action_resend()
        sms_req = self._make_request()
        self._send(sms_req)
        self._age(sms_req)
        sms_send = self._cron()
        self.assertEqual(sms_send.call_count, 1)
        self.assertEqual(sms_req.reminder_count, 1)

    def test_no_email_reminder_without_a_valid_address(self):
        req = self._make_mail_request()
        self._send_mail(req)
        req.sudo().email = "no-longer-valid"
        self._age(req)
        with self.assertLogs(_LOGGER, level="INFO") as log:
            self._cron()
        self.assertEqual(req.reminder_count, 0)
        # The address change removed the first email from the queue (an
        # email still queued carries the old link): no email carries the
        # current link.
        self.assertFalse(self.env["mail.mail"].sudo().search(
            [("body_html", "ilike", req.sudo()._public_url())]))
        # The exact reason: since each email reminder runs under its own
        # savepoint, a reminder attempted then aborted also says "skipped",
        # but at ERROR level.
        self.assertTrue(
            [line for line in log.output
             if req.name in line and "address missing or invalid" in line],
            "a skipped request must leave a trace in the server log")
        self.assertFalse([line for line in log.output
                          if line.startswith("ERROR")])

    def test_a_request_that_raises_does_not_stop_the_others(self):
        """Each email reminder runs under a savepoint. The faulty one undoes
        nothing, blocks nobody, and what it had started writing goes away with
        it."""
        faulty = self._make_mail_request()
        self._send_mail(faulty)
        healthy = self._make_mail_request(email="healthy@example.com")
        self._send_mail(healthy)
        # The faulty request comes first in the job's order.
        self._age(faulty, 26)
        self._age(healthy, 25)
        Model = type(self.env["bf.document.request"])
        original = Model._send_link_email

        def flaky(this, reminder=False):
            if this == faulty:
                this.env["mail.mail"].sudo().create({
                    "email_to": "trace@example.com",
                    "subject": "Trace of an aborted reminder",
                    "body_html": "<p>trace</p>",
                })
                raise RuntimeError("badly configured brand")
            return original(this, reminder=reminder)

        with patch.object(Model, "_send_link_email", flaky), \
                self.assertLogs(_LOGGER, level="ERROR") as log:
            self._cron()
        self.assertEqual(faulty.reminder_count, 0)
        self.assertEqual(healthy.reminder_count, 1)
        self.assertEqual(len(self._mails("healthy@example.com")), 2)
        self.assertFalse(
            self._mails("trace@example.com"),
            "what the faulty request wrote goes away with its savepoint")
        self.assertIn(faulty.name, "\n".join(log.output))


class TestChannelMigration(BfDocumentEmailCommon):
    """18.0.1.1.0: a request already sent could only have gone out by text
    message."""

    def _migrate(self):
        import importlib.util
        from odoo.tools import file_path
        # The migration is SQL: what the ORM keeps pending must be written
        # first, as during a real upgrade.
        self.env.flush_all()
        path = file_path(
            "bf_document_request/migrations/18.0.1.1.0/post-migrate.py")
        spec = importlib.util.spec_from_file_location("bf_dr_migr_1_1_0", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, "18.0.1.0.2")
        self.env.invalidate_all()

    def test_a_sent_request_gets_text_message_back(self):
        req = self._make_mail_request()
        self._send_mail(req)
        self._migrate()
        self.assertEqual(req.channel, "sms")

    def test_a_draft_keeps_its_channel_and_receives_the_client_address(self):
        self.partner.email = "contact@example.com"
        req = self._make_mail_request(email=False)
        self._migrate()
        self.assertEqual(req.channel, "email")
        self.assertEqual(req.email, "contact@example.com")

    def test_an_address_already_entered_is_not_replaced(self):
        self.partner.email = "contact@example.com"
        req = self._make_mail_request(email="entered@example.com")
        self._migrate()
        self.assertEqual(req.email, "entered@example.com")

    def test_a_fresh_install_touches_nothing(self):
        req = self._make_mail_request()
        self._send_mail(req)
        import importlib.util
        from odoo.tools import file_path
        path = file_path(
            "bf_document_request/migrations/18.0.1.1.0/post-migrate.py")
        self.env.flush_all()
        spec = importlib.util.spec_from_file_location("bf_dr_migr_fresh", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, None)
        self.env.invalidate_all()
        self.assertEqual(req.channel, "email")


# ═══════════════════════════════════════ destination of a sent request ══
class TestDestinationGuard(BfDocumentEmailCommon):
    """After sending, the channel, the address and the mobile number decide
    where the NEXT link goes. A colleague who could change them, then resend,
    would get the link to a file that is not theirs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        internal = cls.env.ref("base.group_user").id
        cls.colleague = cls.env["res.users"].create({
            "name": "Colleague",
            "login": "bf_document_colleague_test",
            "email": "colleague@example.com",
            "lang": "en_US",
            "groups_id": [(4, internal), (4, cls.env.ref(
                "bf_securetransfer.group_securetransfer_user").id)],
        })
        cls.manager = cls.env["res.users"].create({
            "name": "Transfer manager",
            "login": "bf_document_manager_test",
            "email": "manager@example.com",
            "lang": "en_US",
            "groups_id": [(4, internal), (4, cls.env.ref(
                "bf_securetransfer.group_securetransfer_manager").id)],
        })

    def _sent(self):
        req = self._make_mail_request()
        self._send_mail(req)
        return req

    def _changes(self, req):
        return req.transfer_id.sudo().access_log_ids.filtered(
            lambda entry: entry.action == "bf_document_request_destination_changed")

    def test_a_colleague_cannot_divert_the_link(self):
        req = self._sent()
        token = req.sudo().token
        for vals in ({"email": "pirate@example.com"}, {"channel": "sms"},
                     {"phone": "438-555-0100"}):
            with self.assertRaises(UserError):
                req.with_user(self.colleague).write(vals)
        self.assertEqual(req.email, self.ADDRESS)
        self.assertEqual(req.channel, "email")
        self.assertEqual(req.sudo().token, token)

    def test_a_colleague_cannot_make_themselves_requester(self):
        """Otherwise the guard is bypassed in two steps: become the requester,
        then change the address."""
        req = self._sent()
        with self.assertRaises(UserError):
            req.with_user(self.colleague).write({"user_id": self.colleague.id})
        self.assertEqual(req.user_id, self.employee)

    def test_the_requester_can_hand_over(self):
        req = self._sent()
        token = req.sudo().token
        self.assertEqual(req.transfer_id.recipient_emails, "employee@example.com")
        req.with_user(self.employee).write({"user_id": self.colleague.id})
        self.assertEqual(req.user_id, self.colleague)
        self.assertEqual(req.sudo().token, token, "the link itself has not moved")
        # The new requester is the one who will receive the documents on
        # submission, no longer the first one.
        self.assertEqual(req.transfer_id.recipient_emails, "colleague@example.com")
        self.assertEqual(req.transfer_id.sender_email, self.ADDRESS)

    def test_by_text_message_handing_over_also_moves_the_fallback_receipt(self):
        """Without an email on the client's contact, the base's receipt went
        to the requester: it follows the new one."""
        req = self._make_request()
        self._send(req)
        self.assertEqual(req.transfer_id.sender_email, "employee@example.com")
        req.with_user(self.employee).write({"user_id": self.colleague.id})
        self.assertEqual(req.transfer_id.recipient_emails, "colleague@example.com")
        self.assertEqual(req.transfer_id.sender_email, "colleague@example.com")

    def test_no_handover_to_a_requester_without_email(self):
        """Same rule as when sending: they could not receive the documents."""
        no_email = self.env["res.users"].create({
            "name": "Employee without email",
            "login": "bf_document_no_email_test",
            "lang": "en_US",
            "groups_id": [(4, self.env.ref("base.group_user").id), (4, self.env.ref(
                "bf_securetransfer.group_securetransfer_user").id)],
        })
        req = self._sent()
        with self.assertRaises(UserError):
            req.with_user(self.employee).write({"user_id": no_email.id})
        self.assertEqual(req.user_id, self.employee)
        self.assertEqual(req.transfer_id.recipient_emails, "employee@example.com")

    def test_a_draft_stays_free(self):
        req = self._make_mail_request()
        req.with_user(self.colleague).write({"email": "other@example.com"})
        self.assertEqual(req.email, "other@example.com")

    def test_the_requester_changes_the_address_and_the_link_rotates(self):
        req = self._sent()
        old_token = req.sudo().token
        req.with_user(self.employee).write({"email": "new@example.com"})
        new_token = req.sudo().token
        self.assertTrue(new_token)
        self.assertNotEqual(new_token, old_token, "the old link must die")
        self.assertFalse(self.env["bf.document.request"].sudo().search(
            [("token", "=", old_token)]))
        # Nothing goes out on the spot: the new link waits for the next
        # reminder, manual or automatic.
        self.assertFalse(self._mails("new@example.com"))
        # The hash-chained log keeps the address masked...
        entry = self._changes(req)
        self.assertEqual(len(entry), 1)
        self.assertIn("n•••@example.com", entry.note)
        self.assertNotIn("new@example.com", entry.note)
        # ... the chatter the address in clear, and never the link.
        body = " ".join(req.message_ids.mapped("body"))
        self.assertIn("new@example.com", body)
        self.assertNotIn(new_token, body)
        self.assertNotIn(old_token, body)
        # The chatter does not promise that nothing will go out before
        # "Resend": the job, if active, will send the new link.
        self.assertIn("next reminder", body)
        self.assertIn("automatic", body)
        self.assertNotIn("to send the new one", body)
        # The reminder carries the new link, to the new address.
        req.with_user(self.employee).action_resend()
        self.assertIn(new_token, self._mails("new@example.com")[-1].body_html)

    def test_a_manager_can_correct_the_address(self):
        req = self._sent()
        old_token = req.sudo().token
        req.with_user(self.manager).write({"email": "corrected@example.com"})
        self.assertEqual(req.email, "corrected@example.com")
        self.assertNotEqual(req.sudo().token, old_token)

    def test_changing_the_mobile_also_rotates_the_link(self):
        req = self._make_request()
        self._send(req)
        old_token = req.sudo().token
        req.with_user(self.employee).write({"phone": "438-555-0123"})
        self.assertNotEqual(req.sudo().token, old_token)
        self.assertIn("••••••0123", self._changes(req).note)

    def test_changing_the_channel_rotates_the_link(self):
        req = self._sent()
        old_token = req.sudo().token
        req.with_user(self.employee).write({
            "channel": "sms", "phone": "438-555-0123"})
        self.assertNotEqual(req.sudo().token, old_token)

    def test_what_does_not_change_the_destination_keeps_the_link(self):
        """The form may send back an unchanged value, and correcting the
        mobile of an email request does not change where the link goes."""
        req = self._sent()
        token = req.sudo().token
        req.with_user(self.employee).write({"email": self.ADDRESS})
        req.with_user(self.employee).write({"phone": "438-555-0123"})
        self.assertEqual(req.sudo().token, token)
        self.assertFalse(self._changes(req))

    def test_case_or_formatting_does_not_rotate_the_link(self):
        """The destination is compared normalised. Writing the address in
        capitals, or the mobile with parentheses, does not change where the
        link goes: the client keeps theirs."""
        req = self._sent()
        token = req.sudo().token
        req.with_user(self.employee).write({"email": " " + self.ADDRESS.upper()})
        self.assertEqual(req.sudo().token, token)
        by_sms = self._make_request()
        self._send(by_sms)
        sms_token = by_sms.sudo().token
        by_sms.with_user(self.employee).write({"phone": "+1 (514) 555-0199"})
        self.assertEqual(by_sms.sudo().token, sms_token)
        self.assertFalse(self._changes(req) | self._changes(by_sms))
        # A real new destination, on the other hand, still rotates the link.
        by_sms.with_user(self.employee).write({"phone": "(438) 555-0123"})
        self.assertNotEqual(by_sms.sudo().token, sms_token)

    # ── link rotation on closed or forged requests ──────────────────────────
    def test_the_link_only_rotates_on_an_open_request(self):
        """It takes the transfer, the sending date AND an open status. On a
        draft, on a forged "sent" request missing one of the two, or on a
        closed request, no token is minted."""
        draft = self._make_mail_request()
        no_transfer = self._make_mail_request()
        no_transfer.sudo().write({
            "state": "sent", "sent_at": fields.Datetime.now()})
        no_date = self._sent()
        no_date.sudo().sent_at = False
        closed = self._sent()
        with patch.object(type(closed.transfer_id), "_purge_s3", return_value=True):
            closed.action_cancel()
        for req in (draft, no_transfer, no_date, closed):
            token = req.sudo().token
            with self.assertRaises(UserError):
                req._rotate_link()
            self.assertEqual(req.sudo().token, token)
            self.assertFalse(self._changes(req))

    def test_the_receipt_follows_the_new_address(self):
        """The base sends the upload receipt to the transfer's "sender". After
        an address change, it went to the old one, the very address that had
        just been removed."""
        req = self._sent()
        self.assertEqual(req.transfer_id.sender_email, self.ADDRESS)
        req.with_user(self.employee).write({"email": "new@example.com"})
        self.assertEqual(req.transfer_id.sender_email, "new@example.com")
        # An invalid address will not receive the link: the receipt does not
        # go back to the old one for all that, it goes to the requester.
        req.with_user(self.employee).write({"email": "no-longer-valid"})
        self.assertEqual(req.transfer_id.sender_email, "employee@example.com")

    def test_by_text_message_the_receipt_goes_to_the_contact_else_the_requester(self):
        """Same computation as when sending by text message."""
        self.partner.email = "contact@example.com"
        req = self._sent()
        req.with_user(self.employee).write({
            "channel": "sms", "phone": "438-555-0123"})
        self.assertEqual(req.transfer_id.sender_email, "contact@example.com")
        self.partner.email = False
        no_contact = self._sent()
        no_contact.with_user(self.employee).write({
            "channel": "sms", "phone": "438-555-0123"})
        self.assertEqual(no_contact.transfer_id.sender_email,
                         "employee@example.com")

    def test_an_email_still_queued_no_longer_goes_out(self):
        """It carries the old link, to the old address."""
        req = self._sent()
        queued = self._mails()
        self.assertEqual(queued.state, "outgoing")
        req.with_user(self.employee).write({"email": "new@example.com"})
        self.assertFalse(queued.exists())
        self.assertFalse(self._mails(), "nothing goes out to the old address anymore")
        # A deliberate removal is not a failure: no banner.
        self.assertEqual(req.link_mail_state, "none")

    def test_a_failed_email_stays_after_the_change(self):
        """Only the QUEUED email is removed: a failure stays visible, and the
        banner still says to resend."""
        req = self._sent()
        self._mails().write({"state": "exception",
                             "failure_reason": "550 unknown mailbox"})
        req.with_user(self.employee).write({"email": "new@example.com"})
        self.env.invalidate_all()
        self.assertTrue(self._mails())
        self.assertEqual(req.link_mail_state, "failed")


# ═════════════════════════════════════════════ link email failure ══
class TestLinkEmailFailure(BfDocumentEmailCommon):
    """The email deletes itself once sent; a failed email stays, and the
    request reports it to its requester without opening the email to them."""

    def _sent(self):
        req = self._make_mail_request()
        self._send_mail(req)
        return req

    def test_the_request_keeps_its_last_email(self):
        req = self._sent()
        self.assertEqual(req.sudo().link_mail_id, self._mails())
        self.assertEqual(req.link_mail_state, "queued")
        req.with_user(self.employee).action_resend()
        self.assertEqual(req.sudo().link_mail_id, self._mails()[-1])

    def test_a_failure_is_visible_to_the_requester(self):
        req = self._sent()
        self._mails().write({"state": "exception",
                             "failure_reason": "550 unknown mailbox"})
        # Cache cleared: the requester must COMPUTE the status, not read it
        # again.
        self.env.invalidate_all()
        self.assertEqual(req.with_user(self.employee).link_mail_state, "failed")

    def test_the_requester_does_not_read_the_email(self):
        """The email body carries the link: the field is restricted to
        administrators."""
        req = self._sent()
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            req.with_user(self.employee).read(["link_mail_id"])

    def test_a_sent_email_leaves_nothing(self):
        req = self._sent()
        self._mails().unlink()  # what ``auto_delete`` does once sent
        self.assertFalse(req.sudo().link_mail_id)
        self.assertEqual(req.link_mail_state, "none")

    def test_the_form_carries_the_banner(self):
        arch = self.env["bf.document.request"].get_view(view_type="form")["arch"]
        self.assertIn('name="link_mail_failed"', arch)
        self.assertIn("check the", arch)

    def test_the_banner_says_what_it_does_not_see(self):
        """A bounce that arrives after the email was accepted does not go
        through the queue; the banner must not promise to see it."""
        arch = self.env["bf.document.request"].get_view(view_type="form")["arch"]
        self.assertIn("immediate refusals", arch)
        self.assertIn("bounced later", arch)


# ════════════════════════════════════════════════ base's sender quota ══
class TestSenderQuota(BfDocumentEmailCommon):
    """The base caps SPONTANEOUS sendings per sender address. A document
    request is not one: it is not subject to the cap, and does not count among
    that address's sendings."""

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.quota_daily_transfers_per_sender", "2")

    def _spontaneous(self, address):
        return self.env["secure.transfer"].create({
            "brand_id": self.brand.id,
            "sender_name": "Spontaneous sending",
            "sender_email": address,
            "recipient_emails": "recipient@example.com",
        })

    def test_a_request_upload_escapes_the_quota(self):
        # The client already made two spontaneous sendings today, and was
        # asked for documents twice: their upload still goes through.
        self._spontaneous(self.ADDRESS)
        self._spontaneous(self.ADDRESS)
        other = self._make_mail_request()
        self._send_mail(other)
        req = self._make_mail_request()
        self._send_mail(req)
        self._upload(req, req.line_ids[0])
        self._submit(req)
        self.assertEqual(req.state, "received")

    def test_requests_do_not_count_among_the_address_sendings(self):
        for _i in range(2):
            self._send_mail(self._make_mail_request())
        # The public page, for a spontaneous sending from the same address:
        # does not raise.
        self.env["secure.transfer"]._check_sender_quota(self.ADDRESS)

    def test_the_quota_still_holds_for_spontaneous_sendings(self):
        self._spontaneous("spontaneous@example.com")
        self._spontaneous("spontaneous@example.com")
        with self.assertRaises(UserError):
            self.env["secure.transfer"]._check_sender_quota(
                "spontaneous@example.com")


# ═════════════════════════════════════════ state fields, outside sudo ══
class TestStateFields(BfDocumentEmailCommon):
    """A request's status, transfer, token, dates and reminder counters are
    only written under sudo, by the module's actions.

    Writable over RPC, they bypassed the destination guard: put someone
    else's request back to draft, change its address, mark it "sent" again and
    resend it; or create a "sent" request on someone else's draft transfer,
    which a cancellation would then have purged.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        internal = cls.env.ref("base.group_user").id
        cls.colleague = cls.env["res.users"].create({
            "name": "Colleague (state)",
            "login": "bf_document_colleague_state_test",
            "email": "colleague.state@example.com",
            "lang": "en_US",
            "groups_id": [(4, internal), (4, cls.env.ref(
                "bf_securetransfer.group_securetransfer_user").id)],
        })
        cls.manager = cls.env["res.users"].create({
            "name": "Transfer manager (state)",
            "login": "bf_document_manager_state_test",
            "email": "manager.state@example.com",
            "lang": "en_US",
            "groups_id": [(4, internal), (4, cls.env.ref(
                "bf_securetransfer.group_securetransfer_manager").id)],
        })

    def _sent(self):
        req = self._make_mail_request()
        self._send_mail(req)
        return req

    def _values(self, **extra):
        vals = {
            "partner_id": self.partner.id,
            "channel": "email",
            "email": "pirate@example.com",
            "brand_id": self.brand.id,
            "user_id": self.colleague.id,
            "line_ids": [(0, 0, {"document_type_id": self.doc_long.id})],
        }
        vals.update(extra)
        return vals

    def test_a_colleague_cannot_send_someone_elses_request_back_through_draft(self):
        """The reviewer's first scenario: back to draft, change the address,
        mark it "sent" again, resend. Each step is refused."""
        req = self._sent()
        token = req.sudo().token
        as_colleague = req.with_user(self.colleague)
        for vals in ({"state": "draft"},
                     {"state": "draft", "email": "pirate@example.com"}):
            with self.assertRaises(AccessError):
                as_colleague.write(vals)
        self.assertEqual(req.state, "sent")
        self.assertEqual(req.email, self.ADDRESS)
        self.assertEqual(req.sudo().token, token)
        # The third step, on its own: a draft does not become "sent" by hand,
        # nor on someone else's transfer.
        draft = self._make_mail_request(email="pirate@example.com")
        for vals in ({"state": "sent"},
                     {"state": "sent", "transfer_id": req.transfer_id.id,
                      "sent_at": fields.Datetime.now()}):
            with self.assertRaises(AccessError):
                draft.with_user(self.colleague).write(vals)
        self.assertEqual(draft.state, "draft")
        self.assertFalse(draft.transfer_id)
        self.assertFalse(self._mails("pirate@example.com"))

    def test_a_colleague_cannot_create_a_sent_request_on_someone_elses_transfer(self):
        """The reviewer's second scenario. It also closes the purge of
        someone else's draft through the cancellation of a forged request."""
        req = self._sent()
        Request = self.env["bf.document.request"].with_user(self.colleague)
        now = fields.Datetime.now()
        for extra in (
            {"state": "sent", "transfer_id": req.transfer_id.id,
             "sent_at": now},
            {"state": "partial"},
            {"transfer_id": req.transfer_id.id},
            {"sent_at": now},
            {"completed_at": now},
            {"reminder_count": 1},
            {"last_reminder_at": now},
        ):
            with self.assertRaises(AccessError):
                Request.create(self._values(**extra))
        self.assertEqual(req.transfer_id.bf_document_request_ids, req)
        self.assertEqual(req.transfer_id.state, "draft")

    def test_neither_the_requester_nor_a_manager_writes_the_state(self):
        """Outside sudo, nobody: the buttons are the way."""
        req = self._sent()
        req.sudo().write({"reminder_count": 2,
                          "last_reminder_at": fields.Datetime.now()})
        token = req.sudo().token
        for user, vals in (
            (self.employee, {"reminder_count": 0}),
            (self.employee, {"last_reminder_at": False}),
            (self.employee, {"sent_at": fields.Datetime.now() + timedelta(days=1)}),
            (self.employee, {"completed_at": fields.Datetime.now()}),
            (self.employee, {"state": "received"}),
            (self.manager, {"token": "0" * 32}),
            (self.manager, {"state": "draft"}),
        ):
            with self.assertRaises(AccessError):
                req.with_user(user).write(vals)
        self.assertEqual(req.state, "sent")
        self.assertEqual(req.reminder_count, 2)
        self.assertEqual(req.sudo().token, token)

    def test_the_form_still_creates_and_saves(self):
        """The form sends "draft" and empty values on creation, and may send
        back an unchanged value, even as text."""
        created = self.env["bf.document.request"].with_user(self.colleague).create(
            self._values(email=self.ADDRESS, state="draft", reminder_count=0,
                         transfer_id=False, sent_at=False))
        self.assertEqual(created.state, "draft")
        req = self._sent()
        req.with_user(self.employee).write({
            "state": "sent",
            "sent_at": fields.Datetime.to_string(req.sent_at),
            "transfer_id": req.transfer_id.id,
            "deadline": fields.Datetime.now() + timedelta(days=3),
        })
        self.assertEqual(req.state, "sent")
        self.assertTrue(req.deadline)

    def test_the_legitimate_journey_still_writes_the_state(self):
        """Sending and reminding as the requester; upload and submission as
        the public controller does them (public user, under sudo);
        cancellation as the requester. Each action writes these fields, and
        goes through."""
        req = self._sent()
        req.with_user(self.employee).action_resend()
        self.assertTrue(req.last_reminder_at)
        as_public = req.with_user(self.env.ref("base.public_user")).sudo()
        self._upload(as_public, as_public.line_ids[0])
        self.assertEqual(req.state, "partial")
        self._submit(as_public)
        self.assertEqual(req.state, "received")
        self.assertTrue(req.completed_at)
        other = self._sent()
        with patch.object(type(other.transfer_id), "_purge_s3", return_value=True):
            other.with_user(self.employee).action_cancel()
        self.assertEqual(other.state, "cancelled")
        self.assertFalse(other.sudo().token)

    # ── context defaults ────────────────────────────────────────────────────
    def test_context_defaults_do_not_make_a_sent_request(self):
        """The ORM adds the context's ``default_<field>`` keys AFTER ``vals``
        is read. Outside sudo, those of the state fields are removed: the
        request is born a draft, without a transfer. The other context
        defaults still hold."""
        req = self._sent()
        now = fields.Datetime.now()
        created = self.env["bf.document.request"].with_user(self.colleague).with_context(
            default_state="sent", default_transfer_id=req.transfer_id.id,
            default_token="0" * 32, default_sent_at=now,
            default_completed_at=now, default_reminder_count=1,
            default_last_reminder_at=now,
            default_message="A note from the context",
        ).create(self._values())
        new = created.sudo()
        self.assertEqual(new.state, "draft")
        self.assertFalse(new.transfer_id)
        self.assertFalse(new.token)
        self.assertFalse(new.sent_at or new.completed_at
                         or new.last_reminder_at)
        self.assertEqual(new.reminder_count, 0)
        self.assertEqual(new.message, "A note from the context")
        self.assertEqual(req.transfer_id.bf_document_request_ids, req)

    def test_a_personal_default_does_not_make_a_sent_request(self):
        """Belt and braces: a personal default (``ir.default``, which any
        internal user sets for themselves) is neither in ``vals`` nor in the
        context. The check after creation refuses it."""
        req = self._sent()
        Default = self.env["ir.default"]
        Default.set("bf.document.request", "state", "sent",
                    user_id=self.colleague.id)
        Default.set("bf.document.request", "transfer_id", req.transfer_id.id,
                    user_id=self.colleague.id)
        with self.assertRaises(AccessError):
            self.env["bf.document.request"].with_user(self.colleague).create(
                self._values())
        self.assertEqual(req.transfer_id.bf_document_request_ids, req)
        # The default is theirs only: another user still creates.
        created = self.env["bf.document.request"].with_user(self.employee).create(
            self._values(user_id=self.employee.id))
        self.assertEqual(created.state, "draft")

    def test_a_refused_creation_consumes_no_number(self):
        """The refusal of the state fields happens BEFORE the sequence number:
        a PostgreSQL sequence does not roll back with the transaction, and each
        forged request would leave a gap in the numbering."""
        sequence = self.env.ref("bf_document_request.seq_bf_document_request")
        before = sequence.number_next_actual
        with self.assertRaises(AccessError):
            self.env["bf.document.request"].with_user(self.colleague).create(
                self._values(state="sent"))
        sequence.invalidate_recordset()
        self.assertEqual(sequence.number_next_actual, before)


# ════════════════════════════════════ requested documents, outside sudo ══
class TestLineFields(BfDocumentEmailCommon):
    """A document's file, status and date received are only written under
    sudo, by the client's upload and the submission; a document does not move
    from one request to another; and the upload only touches the files of the
    transfer of ITS request.

    Without these guards, an employee pointed a document of their request to
    another transfer's file, then uploaded again on their page: the photo
    retake deleted that file, storage and record included, under sudo.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        internal = cls.env.ref("base.group_user").id
        cls.colleague = cls.env["res.users"].create({
            "name": "Colleague (documents)",
            "login": "bf_document_colleague_line_test",
            "email": "colleague.line@example.com",
            "lang": "en_US",
            "groups_id": [(4, internal), (4, cls.env.ref(
                "bf_securetransfer.group_securetransfer_user").id)],
        })
        cls.manager = cls.env["res.users"].create({
            "name": "Transfer manager (documents)",
            "login": "bf_document_manager_line_test",
            "email": "manager.line@example.com",
            "lang": "en_US",
            "groups_id": [(4, internal), (4, cls.env.ref(
                "bf_securetransfer.group_securetransfer_manager").id)],
        })

    def _sent(self, doc_types=None, **extra):
        req = self._make_mail_request(doc_types, **extra)
        self._send_mail(req)
        return req

    def _someone_elses_file(self):
        """Another request, whose document is uploaded: its file lives on
        another transfer."""
        other = self._sent([self.doc_short], email="other.client@example.com")
        rec_file = self._upload(other, other.line_ids)
        return other, rec_file

    def _forged_line(self):
        """A document that points to another transfer's file, as the former
        guard let it be written (set here under sudo)."""
        other, rec_file = self._someone_elses_file()
        req = self._sent()
        line = req.line_ids
        line.sudo().write({"file_id": rec_file.id})
        return req, line, other, rec_file

    def _intact(self, other, rec_file):
        self.assertTrue(rec_file.exists(), "the other request's file must stay")
        self.assertEqual(other.line_ids.file_id, rec_file)
        self.assertEqual(other.line_ids.state, "received")

    # ── writing and creating outside sudo ───────────────────────────────────
    def test_nobody_writes_a_document_fields_outside_sudo(self):
        """Neither a colleague, nor the requester, nor a manager: the client's
        page is the way. Directly or through the request."""
        other, rec_file = self._someone_elses_file()
        req = self._sent()
        line = req.line_ids
        now = fields.Datetime.now()
        for user in (self.colleague, self.employee, self.manager):
            for vals in ({"file_id": rec_file.id}, {"state": "received"},
                         {"state": "skipped"}, {"received_at": now}):
                with self.assertRaises(AccessError):
                    line.with_user(user).write(vals)
                with self.assertRaises(AccessError):
                    req.with_user(user).write(
                        {"line_ids": [(1, line.id, vals)]})
        self.assertFalse(line.file_id)
        self.assertEqual(line.state, "pending")
        self.assertFalse(line.received_at)
        self._intact(other, rec_file)

    def test_a_document_does_not_change_request(self):
        """Directly, or by attaching it to one's own request."""
        req = self._sent()
        line = req.line_ids
        theirs = self._make_mail_request(
            [self.doc_short], user_id=self.colleague.id)
        with self.assertRaises(AccessError):
            line.with_user(self.colleague).write({"request_id": theirs.id})
        with self.assertRaises(AccessError):
            theirs.with_user(self.colleague).write({"line_ids": [(4, line.id)]})
        with self.assertRaises(AccessError):
            line.with_user(self.manager).write({"request_id": theirs.id})
        self.assertEqual(line.request_id, req)
        self.assertEqual(theirs.line_ids.document_type_id, self.doc_short)

    def test_a_document_is_not_born_received(self):
        other, rec_file = self._someone_elses_file()
        draft = self._make_mail_request()
        Line = self.env["bf.document.request.line"].with_user(self.colleague)
        now = fields.Datetime.now()
        for extra in ({"file_id": rec_file.id}, {"state": "received"},
                      {"received_at": now}):
            vals = {"request_id": draft.id,
                    "document_type_id": self.doc_short.id, **extra}
            with self.assertRaises(AccessError):
                Line.create(vals)
            with self.assertRaises(AccessError):
                draft.with_user(self.colleague).write({"line_ids": [(0, 0, {
                    "document_type_id": self.doc_short.id, **extra})]})
        with self.assertRaises(AccessError):
            self.env["bf.document.request"].with_user(self.colleague).create({
                "partner_id": self.partner.id, "channel": "email",
                "email": self.ADDRESS, "brand_id": self.brand.id,
                "line_ids": [(0, 0, {"document_type_id": self.doc_short.id,
                                     "file_id": rec_file.id})],
            })
        self.assertEqual(draft.line_ids.document_type_id, self.doc_long)
        self._intact(other, rec_file)

    def test_context_defaults_do_not_make_a_received_document(self):
        """The same holds for a document: its context defaults are removed,
        the others still hold."""
        other, rec_file = self._someone_elses_file()
        draft = self._make_mail_request()
        line = self.env["bf.document.request.line"].with_user(
            self.colleague).with_context(
            default_state="received", default_file_id=rec_file.id,
            default_received_at=fields.Datetime.now(), default_required=False,
        ).create({"request_id": draft.id,
                  "document_type_id": self.doc_short.id})
        self.assertEqual(line.state, "pending")
        self.assertFalse(line.file_id)
        self.assertFalse(line.received_at)
        self.assertFalse(line.required)
        self._intact(other, rec_file)

    def test_a_personal_default_does_not_make_a_received_document(self):
        self.env["ir.default"].set("bf.document.request.line", "state",
                                   "received", user_id=self.colleague.id)
        draft = self._make_mail_request()
        with self.assertRaises(AccessError):
            self.env["bf.document.request.line"].with_user(self.colleague).create({
                "request_id": draft.id,
                "document_type_id": self.doc_short.id})
        self.assertEqual(len(draft.line_ids), 1)

    # ── what still goes through ─────────────────────────────────────────────
    def test_the_form_adds_removes_and_reorders_documents(self):
        """The real form, as a regular employee: creation with documents, then
        adding, removing, reordering and "required"."""
        with Form(self.env["bf.document.request"].with_user(self.colleague)) as f:
            f.partner_id = self.partner
            f.channel = "email"
            f.email = self.ADDRESS
            for doc in (self.doc_long, self.doc_short):
                with f.line_ids.new() as line:
                    line.document_type_id = doc
        req = f.record
        self.assertEqual(req.line_ids.mapped("state"), ["pending", "pending"])
        with Form(req.with_user(self.colleague)) as f:
            f.line_ids.remove(index=0)
            with f.line_ids.new() as line:
                line.document_type_id = self.doc_bare
                line.required = False
            with f.line_ids.edit(0) as line:
                line.sequence = 50
        self.assertEqual(
            req.line_ids.mapped("document_type_id"), self.doc_bare | self.doc_short)
        self.assertEqual(req.line_ids[-1].document_type_id, self.doc_short)
        # What the form sends back as is also goes through, unchanged values
        # included.
        line = req.line_ids[0]
        line.with_user(self.colleague).write({
            "sequence": 1, "required": True, "document_type_id": self.doc_long.id,
            "state": "pending", "file_id": False, "received_at": False,
            "request_id": req.id})
        self.assertEqual(line.document_type_id, self.doc_long)
        self.assertEqual(line.state, "pending")

    def test_the_client_gets_no_receipt_and_the_log_says_so(self):
        """The base would send the client (the transfer's "sender") a receipt
        carrying the download link of their own documents. Only the requester
        receives the link, and the chained log says no receipt went out."""
        req = self._sent([self.doc_short])
        client = req.transfer_id.sudo().sender_email
        self.assertEqual(client, self.ADDRESS)
        Mail = self.env["mail.mail"].sudo()
        to_client = Mail.search_count([("email_to", "ilike", client)])
        to_requester = Mail.search_count([("email_to", "ilike", self.employee.email)])
        self._upload(req, req.line_ids)
        self._submit(req)
        self.assertEqual(req.state, "received")
        self.assertEqual(Mail.search_count([("email_to", "ilike", client)]), to_client,
                         "no receipt to the client")
        self.assertGreater(Mail.search_count([("email_to", "ilike", self.employee.email)]),
                           to_requester, "the requester receives the link")
        emailed = req.transfer_id.sudo().access_log_ids.filtered(
            lambda entry: entry.action == "emailed")
        self.assertEqual(len(emailed), 1)
        self.assertIn("no receipt to the client", emailed.note)
        self.assertNotIn("receipt to: %s" % client, emailed.note)

    def test_a_received_document_cannot_be_removed(self):
        """Its upload is in the transfer's chained log: removing the line
        would leave the log naming a document the request no longer has. A
        document still pending can be removed after sending."""
        req = self._sent([self.doc_short, self.doc_long])
        received, pending = req.line_ids
        self._upload(req, received)
        self.assertEqual(received.state, "received")
        with self.assertRaises(UserError):
            received.with_user(self.colleague).unlink()
        self.assertTrue(received.exists())
        with self.assertRaises(UserError):
            with Form(req.with_user(self.colleague)) as f:
                f.line_ids.remove(index=0)
        self.assertTrue(received.exists())
        pending.with_user(self.colleague).unlink()
        self.assertFalse(pending.exists())
        self.assertEqual(req.line_ids, received)

    def test_the_upload_writes_documents_whoever_the_caller(self):
        """Upload, retake, confirmation and submission write the documents
        under sudo: they do not depend on the public controller's sudo."""
        req = self._sent([self.doc_long, self.doc_short],
                         required=[True, False])
        as_employee = req.with_user(self.employee)
        first = self._upload(as_employee, as_employee.line_ids[0])
        second = self._upload(as_employee, as_employee.line_ids[0])
        self.assertFalse(first.exists())
        self.assertEqual(req.line_ids[0].file_id, second)
        self.assertEqual(req.line_ids[0].state, "received")
        self._submit(as_employee)
        self.assertEqual(req.state, "received")
        self.assertEqual(req.line_ids[1].state, "skipped")

    # ── the upload only touches the files of ITS request ────────────────────
    def test_the_retake_does_not_delete_another_transfer_file(self):
        """``_register_capture`` refuses BEFORE removing the former file."""
        req, line, other, rec_file = self._forged_line()
        with patch.object(type(line), "_drop_file", autospec=True) as removal:
            with self.assertRaises(UserError):
                req._register_capture(line, "photo.jpg", 1024)
        removal.assert_not_called()
        self.assertEqual(line.file_id, rec_file)
        self._intact(other, rec_file)

    def test_removing_another_transfer_file_is_refused(self):
        req, line, other, rec_file = self._forged_line()
        with patch(_FILE + "._s3_delete") as deletion:
            with self.assertRaises(UserError):
                line._drop_file()
        deletion.assert_not_called()
        self._intact(other, rec_file)

    def test_confirming_another_transfer_file_is_refused(self):
        req, line, other, rec_file = self._forged_line()
        with patch(_FILE + "._verify_on_s3", return_value=True):
            with self.assertRaises(UserError):
                req._confirm_capture(line)
        self.assertEqual(line.state, "pending")
        self.assertFalse(line.received_at)
        self.assertEqual(req.state, "sent")
        self._intact(other, rec_file)


@tagged("post_install", "-at_install")
class TestDeliveredPolicies(TransactionCase):
    """18.0.1.1.1: the delivered policies must take a duration a tier offers,
    otherwise sending a document attached to them fails."""

    # The identifiers the 18.0.1.1.1 migration looks the policies up by. The
    # starter catalogue has had neutral identifiers since 18.0.1.2.0, and an
    # upgraded database keeps these former names as second identifiers of the
    # same records (18.0.1.2.0 pre-migration); a fresh install has none. These
    # tests recreate them, and this is the only place they appear here.
    FORMER_IDS = {
        "policy_vendeur_48h": "bf_document_request.policy_short_24h",
        "policy_directrice_45j": "bf_document_request.policy_long_30d",
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _pin_english(cls)
        IMD = cls.env["ir.model.data"]
        for former, current in cls.FORMER_IDS.items():
            if cls.env.ref("bf_document_request." + former, raise_if_not_found=False):
                continue
            IMD.create({
                "module": "bf_document_request", "name": former,
                "model": "bf.document.expiry.policy",
                "res_id": cls.env.ref(current).id, "noupdate": True,
            })

    def test_delivered_durations_are_offered(self):
        short = self.env.ref("bf_document_request.policy_short_24h")
        long_ = self.env.ref("bf_document_request.policy_long_30d")
        Brand = self.env["secure.transfer.brand"]
        free = Brand.create({"name": "Free brand", "slug": "test-free",
                             "tier": "free"})
        ceiling_30 = Brand.create({"name": "30-day brand", "slug": "test-ceiling-30",
                                   "tier": "free", "max_retention_days": 30})
        # 24 h on every brand; 30 days requires a ceiling of at least 30.
        self.assertIn(short.retention_days, free._effective_limits()["expiry_choices"])
        self.assertIn(long_.retention_days,
                      ceiling_30._effective_limits()["expiry_choices"])

    def _migration(self):
        import importlib.util
        from odoo.tools import file_path
        path = file_path("bf_document_request/migrations/18.0.1.1.1/post-migrate.py")
        spec = importlib.util.spec_from_file_location("bf_document_request_mig_1111", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _state_before(self, policy, name, days):
        # The modification date moved forward without any change, as seen on
        # real databases.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_document_expiry_policy SET retention_days = %s, name = %s::jsonb, "
            "write_date = create_date + interval '4 days' WHERE id = %s",
            (days, json.dumps(name), policy.id))
        self.env.invalidate_all()

    def _names(self, policy):
        """The stored name, every language: read in SQL so that a language
        the database does not have active can still be checked."""
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT name FROM bf_document_expiry_policy WHERE id = %s", (policy.id,))
        return self.env.cr.fetchone()[0]

    def _migrate(self):
        self._migration().migrate(self.env.cr, "18.0.1.1.0")
        self.env.invalidate_all()

    def test_migration_changes_untouched_policies(self):
        short = self.env.ref("bf_document_request.policy_short_24h")
        long_ = self.env.ref("bf_document_request.policy_long_30d")
        # The values that release delivered, in the language it delivered
        # them in: the migration compares them with the database.
        self._state_before(short, {"en_US": "Vendeur — 48 h", "fr_CA": "Vendeur — 48 h"}, 2)
        self._state_before(long_, {"en_US": "Directrice — 45 jours"}, 45)
        self._migrate()
        self.assertEqual(short.retention_days, 1)
        self.assertEqual(self._names(short),
                         {"en_US": "Vendeur — 24 h", "fr_CA": "Vendeur — 24 h"})
        self.assertEqual(long_.retention_days, 30)
        self.assertEqual(self._names(long_)["en_US"], "Directrice — 30 jours")
        # Rerunnable.
        self._migrate()
        self.assertEqual(short.retention_days, 1)

    def test_migration_leaves_changed_policies(self):
        short = self.env.ref("bf_document_request.policy_short_24h")
        long_ = self.env.ref("bf_document_request.policy_long_30d")
        # Changed duration, delivered name; changed name (in French only),
        # delivered duration.
        self._state_before(short, {"en_US": "Vendeur — 48 h"}, 7)
        self._state_before(long_, {"en_US": "Directrice — 45 jours",
                                   "fr_CA": "Custom name"}, 45)
        self._migrate()
        self.assertEqual(short.retention_days, 7)
        self.assertEqual(self._names(short), {"en_US": "Vendeur — 48 h"})
        self.assertEqual(long_.retention_days, 45)
        self.assertEqual(self._names(long_)["fr_CA"], "Custom name")
