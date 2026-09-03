"""Who can read what, and what a logged-in user cannot reach over RPC.

The module's whole confidentiality story rests on two things the ORM enforces
for us: the share token is manager-only, and the audit trail is read-only even
for managers. Both are one CSV line away from silently opening up, and neither
had a test. The RPC-surface checks matter just as much: a method without a
leading underscore is a public API to anyone holding a session, so a private
helper that quietly became public would hand out OTP codes.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from .common import BaseNeuve

S3_MOD = "odoo.addons.bf_securetransfer.models.s3"


@tagged("post_install", "-at_install")
class TestSecurityAcl(BaseNeuve, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        icp.set_param("bf_securetransfer.quota_daily_bytes_per_ip_mb", "1000000")

        internal = cls.env.ref("base.group_user")
        cls.g_user = cls.env.ref("bf_securetransfer.group_securetransfer_user")
        cls.g_manager = cls.env.ref(
            "bf_securetransfer.group_securetransfer_manager")

        def _mk(login, groups):
            return cls.env["res.users"].create({
                "name": login, "login": login,
                "groups_id": [(6, 0, [g.id for g in groups])],
            })

        cls.outsider = _mk("st-acl-outsider", [internal])
        cls.reader = _mk("st-acl-reader", [internal, cls.g_user])
        cls.manager = _mk("st-acl-manager", [internal, cls.g_manager])

    def _must_refuse(self, call):
        """Assert that ``call`` is refused, whatever flavour of refusal the
        stack produces: the ACL raises AccessError, the model's own guards
        raise UserError, and which one wins depends on the order they run in.
        The point of these tests is that the operation does not go through."""
        try:
            call()
        except (AccessError, UserError):
            return
        self.fail("the operation should have been refused")

    def _active(self):
        t = self.env["secure.transfer"].api_create(
            self.brand, {
                "sender_name": "Test Sender",
                "sender_email": "sender@example.com",
                "recipient_emails": "dest@example.com",
                "message": "Bonjour", "retention_days": 7,
            }, "203.0.113.10", "test-suite/1.0", "fr_CA")
        t._register_file("doc.pdf", 4096)
        sizes = {f.s3_key: int(f.size) for f in t.file_ids}

        def _head(env, key):
            return ({"size": sizes[key], "etag": "e"} if key in sizes else None)
        with patch(S3_MOD + ".head_object", side_effect=_head):
            t.action_finalize()
        return t

    # ------------------------------------------------------------------ model access
    def test_internal_user_outside_the_groups_sees_nothing(self):
        """An ordinary employee must not be able to read other people's
        transfers just by being logged into Odoo."""
        t = self._active()
        with self.assertRaises(AccessError):
            t.with_user(self.outsider).read(["name"])

    def test_user_group_is_read_only(self):
        """The user group is for consulting the module, not editing it. A
        writable user group would let anyone flip a transfer back to active."""
        t = self._active()
        t.with_user(self.reader).read(["name"])  # allowed
        # NB: plain try/except, NOT assertRaises. Odoo's assertRaises rolls its
        # block back to a savepoint with flush=False, which also discards the
        # records this test created — the next assertion then fails on a
        # missing row instead of on the permission it means to check.
        self.env.flush_all()
        self._must_refuse(lambda: t.with_user(self.reader).write(
            {"sender_name": "changed"}))
        # unlink is refused by the model's own guard before the ACL is even
        # consulted, so the type there is UserError — either way, refused.
        self._must_refuse(lambda: t.with_user(self.reader).unlink())

    def test_manager_can_write_transfers(self):
        """The counterpart: managers must retain the operator actions."""
        t = self._active()
        t.with_user(self.manager).write({"sender_name": "changed"})
        self.assertEqual(t.sender_name, "changed")

    def test_audit_log_is_read_only_even_for_managers(self):
        """This is the load-bearing line of the whole Loi 25 claim: the trail
        cannot be edited or deleted by anyone through the ORM, manager
        included. The CSV grants 1,0,0,0 — and the model blocks it again."""
        t = self._active()
        entry = t.access_log_ids[0]
        entry.with_user(self.manager).read(["action"])  # allowed
        self.env.flush_all()
        self._must_refuse(lambda: entry.with_user(self.manager).write(
            {"note": "rewritten"}))
        self._must_refuse(lambda: entry.with_user(self.manager).unlink())

    def test_audit_log_write_is_blocked_even_in_sudo(self):
        """Defense in depth: the ORM override must hold when the ACL is
        bypassed, otherwise any server-side code could rewrite history."""
        t = self._active()
        with self.assertRaises(UserError):
            t.access_log_ids[0].sudo().write({"note": "rewritten"})

    # ------------------------------------------------------------------ field-level groups
    def test_share_token_is_not_readable_by_a_plain_user(self):
        """The token IS the capability. If the user group could read it, every
        employee could download every client's files."""
        t = self._active()
        fields_ = self.env["secure.transfer"].with_user(
            self.reader).fields_get(["token", "upload_token"])
        for fname in ("token", "upload_token"):
            if fname in fields_:
                self.fail("%s must not be exposed to the user group" % fname)

    def test_secrets_are_not_readable_without_system_group(self):
        """Password and OTP hashes are offline-attackable. They are restricted
        to base.group_system, not merely to managers."""
        t = self._active()
        fields_ = self.env["secure.transfer"].with_user(
            self.manager).fields_get(["password_hash", "sender_otp_hash"])
        for fname in ("password_hash", "sender_otp_hash"):
            if fname in fields_:
                self.fail("%s must stay system-only" % fname)

    # ------------------------------------------------------------------ RPC surface
    def test_sensitive_helpers_stay_private(self):
        """A method without a leading underscore is callable by anyone holding
        a session. These five must never lose theirs: the first would hand out
        a recipient's OTP, the last would erase a client's files."""
        model = self.env["secure.transfer"]
        for name in ("_send_recipient_otp", "_set_password", "_check_password",
                     "_activate", "_purge_s3"):
            self.assertTrue(
                hasattr(model, name),
                "%s disappeared — the guard below is then meaningless" % name)
            self.assertTrue(
                name.startswith("_"),
                "%s must keep its underscore: it is RPC-reachable without it"
                % name)

    def test_private_method_is_refused_over_the_rpc_entry_point(self):
        """Not just a naming convention — Odoo's dispatcher itself must refuse
        it. This is the check that would catch a future refactor exposing the
        OTP sender."""
        from odoo.service.model import get_public_method
        t = self._active()
        with self.assertRaises(AccessError):
            get_public_method(t.with_user(self.reader), "_send_recipient_otp")

    def test_public_api_methods_are_reachable_by_design(self):
        """The other side of the coin: the three genuinely public entry points
        must stay callable, or the public page breaks."""
        from odoo.service.model import get_public_method
        t = self._active()
        for name in ("api_create", "action_finalize", "confirm_sender_otp"):
            get_public_method(t.with_user(self.manager), name)

    def test_mpu_sign_refuses_a_bare_string(self):
        """`mpu_sign` is RPC surface (no leading underscore). A bare string
        iterates character by character, so "123" used to be read as parts
        1, 2 and 3 — presigned upload URLs handed out for parts nobody asked
        for. The web controller rejects non-lists, but the model must too."""
        t = self._active()
        f = t.file_ids[0]
        f.write({"upload_mode": "multipart", "s3_upload_id": "u-1",
                 "parts_total": 5, "part_size": 8 * 1024 * 1024})
        t.state = "draft"  # presign is only legal while the draft is open
        with self.assertRaises(UserError):
            f.mpu_sign("123")
        with self.assertRaises(UserError):
            f.mpu_sign(3)

    # ------------------------------------------------------------------ multi-company
    def test_multi_company_rule_hides_another_company_transfer(self):
        """A shared Odoo must not leak transfers across companies."""
        other_co = self.env["res.company"].create({"name": "ST Other Co"})
        t = self._active()
        t.company_id = other_co
        allowed = self.manager.company_ids
        if other_co in allowed:
            self.skipTest("test user already has access to the other company")
        found = self.env["secure.transfer"].with_user(self.manager).search(
            [("id", "=", t.id)])
        self.assertFalse(found, "the multi-company rule did not apply")
