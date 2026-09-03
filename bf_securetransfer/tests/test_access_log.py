"""Append-only chained access trail: _append hashing, verify_chain,
immutability (write blocked, unlink blocked outside the st_gc context)."""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .common import LanguesActives


@tagged("post_install", "-at_install")
class TestSecureTransferAccessLog(LanguesActives, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_ip", "500")
        icp.set_param("bf_securetransfer.quota_daily_transfers_per_sender", "500")
        cls.transfer = cls.env["secure.transfer"].api_create(
            cls.brand,
            {"sender_email": "log@example.com", "retention_days": 7},
            "203.0.113.20", "log-suite/1.0", "fr_CA",
        )

    # ------------------------------------------------------------------ chain
    def test_append_builds_chain(self):
        t = self.transfer
        e1 = t.access_log_ids[:1]  # "created", appended by api_create
        self.assertEqual(e1.action, "created")
        self.assertEqual(e1.prev_entry_hash, "")
        self.assertTrue(e1.entry_hash)

        e2 = t._log("view", ip="203.0.113.21", ua="ua", note="page vue")
        self.assertEqual(e2.prev_entry_hash, e1.entry_hash)
        self.assertNotEqual(e2.entry_hash, e1.entry_hash)
        # deterministic: recomputing the canonical hash matches the stored one
        Log = self.env["secure.transfer.access.log"]
        recomputed = Log._entry_hash(
            e2.prev_entry_hash, t.id, t.name, e2.action, e2.timestamp_utc,
            e2.ip, e2.user_agent, e2.actor, e2.note,
        )
        self.assertEqual(recomputed, e2.entry_hash)

    def test_verify_chain_intact(self):
        t = self.transfer
        t._log("view", ip="1.2.3.4")
        t._log("password_fail", ip="1.2.3.4", note="tentative")
        self.assertTrue(t.access_log_ids[:1].verify_chain())

    def test_verify_chain_detects_tampering(self):
        t = self.transfer
        entry = t._log("download", ip="1.2.3.4", note="doc.pdf")
        self.assertTrue(entry.verify_chain())
        # write() is blocked, so tamper straight in the DB like an attacker
        self.env.cr.execute(
            "UPDATE secure_transfer_access_log SET note = %s WHERE id = %s",
            ("autre-fichier.pdf", entry.id),
        )
        entry.invalidate_recordset()
        self.assertFalse(entry.verify_chain())

    def test_none_and_false_normalize_identically(self):
        """Write path passes None kwargs; the DB hands back False. Both must
        hash to the same value or the chain would never verify."""
        Log = self.env["secure.transfer.access.log"]
        h_none = Log._entry_hash("", 1, "TRF", "view", "ts", None, None, None, None)
        h_false = Log._entry_hash("", 1, "TRF", "view", "ts", False, False, False, False)
        self.assertEqual(h_none, h_false)

    def test_user_agent_truncated(self):
        entry = self.transfer._log("view", ua="A" * 2000)
        self.assertEqual(len(entry.user_agent), 512)
        self.assertTrue(entry.verify_chain())

    # ------------------------------------------------------------------ immutability
    def test_write_blocked(self):
        entry = self.transfer.access_log_ids[:1]
        with self.assertRaises(UserError):
            entry.write({"note": "réécrit"})
        with self.assertRaises(UserError):
            entry.sudo().write({"ip": "6.6.6.6"})

    def test_unlink_blocked_without_gc_context(self):
        entry = self.transfer.access_log_ids[:1]
        with self.assertRaises(UserError):
            entry.unlink()
        self.assertTrue(entry.exists())

    def test_unlink_allowed_under_gc_context(self):
        entry = self.transfer._log("view")
        entry.with_context(st_gc=True).unlink()
        self.assertFalse(entry.exists())
