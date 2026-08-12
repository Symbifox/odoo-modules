import hashlib

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_destruction_register")
class TestPrivacyDestructionRegister(TransactionCase):
    """Test cases for the immutable destruction register (Art. 3.2 LPRPSP)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Register = cls.env["privacy.destruction.register"]
        cls.officer_group = cls.env.ref("privacy_consent.group_privacy_officer")
        cls.manager_group = cls.env.ref("privacy_consent.group_privacy_manager")
        cls.user_group = cls.env.ref("privacy_consent.group_privacy_user")

        cls.officer = cls.env["res.users"].create({
            "name": "Privacy Officer",
            "login": "test_privacy_officer",
            "email": "officer@example.com",
            "groups_id": [(4, cls.officer_group.id)],
        })
        cls.manager_user = cls.env["res.users"].create({
            "name": "Privacy Manager",
            "login": "test_privacy_manager",
            "email": "manager@example.com",
            "groups_id": [(4, cls.manager_group.id)],
        })
        cls.reader = cls.env["res.users"].create({
            "name": "Privacy Reader",
            "login": "test_privacy_reader",
            "email": "reader@example.com",
            "groups_id": [(4, cls.user_group.id)],
        })

    def _make_entry(self):
        return self.Register.create({
            "destroyed_by_id": self.officer.id,
            "approved_by_id": self.officer.id,
            "document_description": "Test document",
            "destruction_method": "delete",
            "legal_basis": "Test",
        })

    def test_register_number_assigned(self):
        """A register_number is auto-assigned on create."""
        entry = self._make_entry()
        self.assertTrue(entry.register_number)

    def test_verification_hash_computed_on_create(self):
        """verification_hash is SHA-256 and matches content."""
        entry = self._make_entry()
        self.assertTrue(entry.verification_hash)
        self.assertEqual(len(entry.verification_hash), 64)
        recomputed = entry._compute_verification_hash()
        self.assertEqual(entry.verification_hash, recomputed)

    def test_write_non_notes_field_raises(self):
        """Writing any field except notes raises UserError (Art. 3.2)."""
        entry = self._make_entry()
        with self.assertRaises(UserError):
            entry.write({"document_description": "tampered"})
        with self.assertRaises(UserError):
            entry.write({"destruction_method": "anonymize"})
        with self.assertRaises(UserError):
            entry.write({"legal_basis": "tampered basis"})

    def test_unlink_raises(self):
        """unlink() raises UserError (Art. 3.2)."""
        entry = self._make_entry()
        with self.assertRaises(UserError):
            entry.unlink()

    def test_notes_editable_by_officer(self):
        """Officer can edit notes (v3.0.1 fix #3)."""
        entry = self._make_entry()
        entry.with_user(self.officer).write({"notes": "Officer note"})
        self.assertEqual(entry.notes, "Officer note")

    def test_notes_not_editable_by_user(self):
        """User group (read-only) cannot write notes."""
        entry = self._make_entry()
        with self.assertRaises(AccessError):
            entry.with_user(self.reader).write({"notes": "Unauthorized"})

    def test_destruction_method_includes_secure_wipe(self):
        """destruction_method selection includes secure_wipe (v3.0.1 fix #2)."""
        selection_vals = dict(
            self.Register._fields["destruction_method"]._description_selection(self.env)
        )
        self.assertIn("secure_wipe", selection_vals)

    def test_hash_chain_previous_hash(self):
        """Each new entry's previous_hash equals the prior entry's hash."""
        # Clear any existing entries in the chain — keep references to prior
        prior = self.Register.search([], order="id desc", limit=1)
        prior_hash = prior.verification_hash if prior else ""

        first = self._make_entry()
        self.assertEqual(first.previous_hash, prior_hash)

        second = self._make_entry()
        self.assertEqual(second.previous_hash, first.verification_hash)

    def test_manual_hash_injection_stripped(self):
        """Passing verification_hash in create vals is silently dropped."""
        forged = "0" * 64
        entry = self.Register.create({
            "destroyed_by_id": self.officer.id,
            "approved_by_id": self.officer.id,
            "document_description": "Forge attempt",
            "destruction_method": "delete",
            "legal_basis": "Test",
            "verification_hash": forged,
            "previous_hash": forged,
        })
        self.assertNotEqual(entry.verification_hash, forged)

    def test_cron_verify_chain_integrity_clean(self):
        """Verification cron reports 0 broken entries on a clean chain."""
        self._make_entry()
        result = self.Register.cron_verify_chain_integrity()
        self.assertEqual(result, 0)

    def test_create_secure_wipe_entry(self):
        """An entry with destruction_method='secure_wipe' can be created."""
        entry = self.Register.create({
            "destroyed_by_id": self.officer.id,
            "approved_by_id": self.officer.id,
            "document_description": "Wiped attachment",
            "destruction_method": "secure_wipe",
            "legal_basis": "Test",
        })
        self.assertEqual(entry.destruction_method, "secure_wipe")
        self.assertTrue(entry.verification_hash)
