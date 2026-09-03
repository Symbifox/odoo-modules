"""What the transfer RETAINS of its own share link, and the retroactive gate.

The module guards the capability twice — ``token`` is manager-only at rest, and
every backend look goes through the journaled reveal wizard. The chatter walked
around both: ``mail.template.send_mail`` stores the rendered body on the record,
so the live link sat there in clear, readable by anyone allowed to read the
transfer, and nothing in the Loi 25 trail said they had read it.

The rule under test: a link that no recipient code gates IS the credential, so
it must not survive in what the record keeps. A link held behind a code opens
nothing on its own and is left alone — and the predicate is re-read on every
sweep, so turning the instance-wide setting off later blots the old messages.

The companion feature is the retroactive gate: arming the recipient code on a
transfer that is already out, which is what makes the rule
actionable instead of merely diagnostic.

Nothing here touches SMTP: the queue is driven by hand and the post-send hook is
called the way mail.mail calls it.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_securetransfer.models.secure_transfer import SHARE_TOKEN_MASK

from .common import LanguesActives


@tagged("post_install", "-at_install")
class TestChatterLinkRetention(LanguesActives, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")
        # Instance-wide gate OFF: each test arms exactly what it means to test.
        icp.set_param("bf_securetransfer.require_recipient_otp", "0")
        icp.set_param("bf_securetransfer.require_sender_otp", "0")
        # Arming the recipient code is a manager action (it writes on a model
        # the user group only reads); the ACL test below builds its own
        # read-only account.
        cls.env.user.groups_id = [(4, cls.env.ref(
            "bf_securetransfer.group_securetransfer_manager").id)]

    _n = 0

    def _addr(self):
        type(self)._n += 1
        return "chatter-dest-%d@example.test" % self._n

    def _sent_transfer(self, **overrides):
        """A message-only transfer, finalized and with its e-mails queued.

        Message-only keeps S3 out of the picture entirely: the link still rides
        every notification, which is all this file is about.
        """
        vals = {
            "sender_name": "Test Sender",
            "sender_email": "sender@example.com",
            "recipient_emails": self._addr(),
            "message": "CONTENU-SENSIBLE",
            "retention_days": 7,
        }
        vals.update(overrides)
        force_otp = vals.pop("force_recipient_otp", False)
        transfer = self.env["secure.transfer"].api_create(
            self.brand, vals, "203.0.113.10", "test-suite/1.0", "fr_CA",
        )
        if force_otp:
            transfer.force_recipient_otp = True
        transfer.action_finalize()
        return transfer

    def _bodies(self, transfer):
        return self.env["mail.message"].sudo().search([
            ("model", "=", "secure.transfer"), ("res_id", "=", transfer.id),
        ]).mapped("body")

    def _queued(self, transfer):
        return self.env["mail.mail"].sudo().search([
            ("model", "=", "secure.transfer"), ("res_id", "=", transfer.id),
        ])

    def _deliver(self, transfer):
        """Play what the queue does: mark the mails sent, then call the hook
        with mail.mail's own signature."""
        mails = self._queued(transfer)
        mails.write({"state": "sent"})
        mails._postprocess_sent_message(success_pids=[])
        self.env.invalidate_all()
        return mails

    def _has_token(self, transfer):
        token = transfer.sudo().token
        return any(token in (body or "") for body in self._bodies(transfer))

    # -------------------------------------------------------------- the rule
    def test_a_queued_email_keeps_its_real_link(self):
        """The body IS what goes out. Blotting it before the queue has sent it
        would deliver a dead link to the recipient — the fix would break the
        product it protects."""
        transfer = self._sent_transfer()
        self.assertTrue(self._queued(transfer), "no e-mail was queued at all")
        self.assertTrue(self._has_token(transfer))
        # A sweep running while the mail is still outgoing must not touch it.
        self.env["secure.transfer"]._cron_redact_chatter_links()
        self.assertTrue(
            self._has_token(transfer),
            "a mail still in the queue was redacted before being sent")

    def test_an_ungated_link_does_not_survive_the_send(self):
        """THE test of this file. With no recipient code, the retained body is
        a bearer credential: whoever reads the transfer downloads the files,
        unlogged and without the reveal wizard."""
        transfer = self._sent_transfer()
        self._deliver(transfer)
        self.assertFalse(
            self._has_token(transfer),
            "the share token survived in the chatter of an ungated transfer")
        self.assertTrue(
            any(SHARE_TOKEN_MASK in (b or "") for b in self._bodies(transfer)),
            "the link vanished entirely instead of being visibly masked")
        self.assertIn(
            "link_redacted", transfer.access_log_ids.mapped("action"),
            "the masking must leave a trace in the Loi 25 trail")

    def test_the_outgoing_copy_is_masked_too(self):
        """mail.mail keeps its own body_html. Redacting only mail.message
        leaves the very same link readable one click away in Technical →
        E-mails."""
        transfer = self._sent_transfer()
        mails = self._deliver(transfer)
        token = transfer.sudo().token
        for mail in mails.exists():
            self.assertNotIn(token, mail.body_html or "")

    def test_a_gated_link_is_left_alone(self):
        """A link held behind a recipient code opens nothing on its own, so the
        operator keeps the convenience of reading it from the chatter. Masking
        it anyway would be a change nobody asked for."""
        transfer = self._sent_transfer(force_recipient_otp=True)
        self._deliver(transfer)
        self.assertTrue(
            self._has_token(transfer),
            "the link was masked even though a recipient code gates it")
        self.assertNotIn("link_redacted",
                         transfer.access_log_ids.mapped("action"))

    def test_dropping_the_instance_gate_blots_the_old_messages(self):
        """The instance-wide setting is what protected these transfers. The day
        it is turned off, every link it was covering becomes a live credential
        sitting in a chatter — the sweep must catch up, not only apply to new
        sends."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.require_recipient_otp", "1")
        transfer = self._sent_transfer()
        self._deliver(transfer)
        self.assertTrue(self._has_token(transfer))
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.require_recipient_otp", "0")
        self.env["secure.transfer"]._cron_redact_chatter_links()
        self.assertFalse(
            self._has_token(transfer),
            "the sweep did not re-evaluate the gate for older messages")

    def test_redaction_is_idempotent(self):
        """The sweep runs hourly for the life of the record: a second pass must
        neither find anything nor keep appending to the trail."""
        transfer = self._sent_transfer()
        self._deliver(transfer)
        before = len(transfer.access_log_ids)
        self.assertEqual(transfer._redact_chatter_links(), 0)
        self.assertEqual(len(transfer.access_log_ids), before)

    def test_force_masks_a_gated_transfer_too(self):
        """The escape hatch for an operator who wants nothing retained, gate or
        no gate."""
        transfer = self._sent_transfer(force_recipient_otp=True)
        self._deliver(transfer)
        self.assertTrue(transfer._redact_chatter_links(force=True))
        self.assertFalse(self._has_token(transfer))

    # -------------------------------------------------------- retroactive gate
    def test_requiring_a_code_after_the_fact_arms_the_gate(self):
        """A transfer already out must be closable. The download
        controller reads _recipient_otp_required(), so arming the per-transfer
        flag is what actually holds the content."""
        transfer = self._sent_transfer()
        self.assertFalse(transfer._recipient_otp_required())
        transfer.action_require_recipient_otp()
        self.assertTrue(transfer.force_recipient_otp)
        self.assertTrue(transfer._recipient_otp_required())
        self.assertIn("otp_forced", transfer.access_log_ids.mapped("action"))

    def test_arming_the_gate_is_journaled_and_idempotent(self):
        """The trail is the evidence that the content was held from a given
        moment. Two clicks must not read as two distinct decisions."""
        transfer = self._sent_transfer()
        transfer.action_require_recipient_otp()
        transfer.action_require_recipient_otp()
        forced = transfer.access_log_ids.filtered(
            lambda entry: entry.action == "otp_forced")
        self.assertEqual(len(forced), 1)
        self.assertEqual(forced.actor, self.env.user.login)

    def test_arming_the_gate_needs_the_manager_group(self):
        """A method without a leading underscore is reachable over XML-RPC, and
        the button's `groups=` guards only the UI. The securetransfer USER group
        is read-only on secure.transfer, so a read-only account must not be able
        to change a transfer through this action."""
        transfer = self._sent_transfer()
        reader = self.env["res.users"].create({
            "name": "st-reader", "login": "st-reader-otp",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_securetransfer.group_securetransfer_user").id,
            ])],
        })
        try:
            transfer.with_user(reader).action_require_recipient_otp()
        except (UserError, AccessError) as exc:
            self.assertNotIn("lien seul", str(exc))
        else:
            self.fail("a read-only user armed the gate")
        transfer.invalidate_recordset(["force_recipient_otp"])
        self.assertFalse(transfer.force_recipient_otp)

    def test_arming_the_gate_on_a_link_only_transfer_is_refused(self):
        """No recipient means nowhere to send the code: arming the gate would
        lock the content away from everyone, including the sender who is
        holding the link."""
        transfer = self._sent_transfer(recipient_emails="")
        try:
            transfer.action_require_recipient_otp()
        except UserError as exc:
            self.assertIn("lien seul", str(exc))
        else:
            self.fail("a link-only transfer must refuse the retroactive gate")
        self.assertFalse(transfer.force_recipient_otp)

    def test_arming_the_gate_on_a_draft_is_refused(self):
        """A draft has no live link and no e-mail out; the button would promise
        a protection that means nothing yet."""
        transfer = self.env["secure.transfer"].api_create(
            self.brand,
            {"sender_email": "sender@example.com",
             "recipient_emails": self._addr(), "message": "x",
             "retention_days": 7},
            "203.0.113.10", "test-suite/1.0", "fr_CA",
        )
        try:
            transfer.action_require_recipient_otp()
        except UserError:
            pass
        else:
            self.fail("a draft must refuse the retroactive gate")

    def test_arming_the_gate_says_what_it_does_not_do(self):
        """The gate closes the LINK; it does not reach into the inboxes the
        notification already landed in. An operator who reads "code required"
        as "the message was recalled" would stop treating an ungated send as
        an incident."""
        transfer = self._sent_transfer()
        transfer.action_require_recipient_otp()
        self.assertTrue(
            any("pas rappelés" in (body or "")
                for body in self._bodies(transfer)),
            "nothing in the chatter says the sent e-mails are not recalled")

    # ------------------------------------------------------- effective gate
    def test_the_form_shows_the_effective_gate_not_the_flag(self):
        """What made this ambiguous in the first place: an instance that holds
        every transfer behind a code leaves force_recipient_otp UNTICKED, so a
        gated transfer read as wide open on the form — and its retained link
        read as a leak that was not one."""
        transfer = self._sent_transfer()
        self.assertEqual(transfer.recipient_otp_status, "off")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.require_recipient_otp", "1")
        transfer.invalidate_recordset(["recipient_otp_status"])
        self.assertEqual(transfer.recipient_otp_status, "instance")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.require_recipient_otp", "0")
        transfer.force_recipient_otp = True
        self.assertEqual(transfer.recipient_otp_status, "transfer")
