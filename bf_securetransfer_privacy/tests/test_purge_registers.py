from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

REAL_PURGE = "odoo.addons.bf_securetransfer.models.secure_transfer.SecureTransfer._purge_s3"
BRIDGE = "odoo.addons.bf_securetransfer_privacy.models.secure_transfer.SecureTransfer"
MODULE = "bf_securetransfer_privacy"


def _fake_purge(this, actor=None):
    """A purge that succeeds, without touching the object storage."""
    this.file_ids.write({"state": "purged"})
    this.write({"state": "deleted"})
    return True


class PurgeCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Register = cls.env["privacy.destruction.register"]
        # `brand_id` is required on secure.transfer; the base module ships a
        # default brand as data.
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        cls.transfer = cls.env["secure.transfer"].create({
            "brand_id": cls.brand.id,
            "sender_name": "Test sender",
            "sender_email": "sender@example.com",
            "recipient_emails": "one@example.com, two@example.com",
        })
        cls.env["secure.transfer.file"].create({
            "transfer_id": cls.transfer.id,
            "filename": "signed-contract.pdf",
            "size": 1024,
            "s3_key": "test/signed-contract.pdf",
        })
        cls.env["secure.transfer.file"].create({
            "transfer_id": cls.transfer.id,
            "filename": "appendix.xlsx",
            "size": 2048,
            "s3_key": "test/appendix.xlsx",
        })

    def _register_entries(self, transfer=None):
        return self.Register.search([
            ("res_model", "=", "secure.transfer"),
            ("res_id", "=", (transfer or self.transfer).id),
        ])


@tagged("post_install", "-at_install", "bf_securetransfer_privacy")
class TestPurgeRegistersDestruction(PurgeCommon):
    """The object storage purge must leave a trace in the destruction register."""

    def test_successful_purge_writes_one_register_entry(self):
        """A successful purge writes exactly one entry, which names the files."""
        with patch(REAL_PURGE, _fake_purge):
            self.transfer._purge_s3()

        entries = self._register_entries()
        self.assertEqual(len(entries), 1)
        entry = entries
        self.assertIn("signed-contract.pdf", entry.document_description)
        self.assertIn("appendix.xlsx", entry.document_description)
        self.assertEqual(entry.res_name, self.transfer.name)
        self.assertEqual(entry.destruction_method, "delete")
        self.assertEqual(entry.subject_count, 2)
        self.assertEqual(
            entry.retention_calendar_id,
            self.env.ref("bf_securetransfer_privacy.retention_securetransfer"))
        self.assertTrue(entry.verification_hash)

    def test_failed_purge_writes_nothing(self):
        """A purge that returns False must certify nothing.

        `_purge_s3` returns False when keys resist: saying "destroyed" in that
        case would claim a destruction that did not happen.
        """
        with patch(REAL_PURGE, lambda this, actor=None: False):
            self.transfer._purge_s3()
        self.assertFalse(self._register_entries())

    def test_second_purge_does_not_recertify_already_purged_files(self):
        """A second pass over an already purged transfer writes nothing.

        This is the freezing trap: the list must be taken BEFORE the purge,
        and a transfer whose files are all `purged` has nothing left to record.
        """
        with patch(REAL_PURGE, _fake_purge):
            self.transfer._purge_s3()
            self.transfer._purge_s3()

        self.assertEqual(len(self._register_entries()), 1)

    def test_register_failure_does_not_break_the_purge(self):
        """If recording fails, the purge still succeeds.

        Recording matters, but not to the point of preventing a requested
        destruction. The scheduled job must carry on with its round.
        """
        def boom(this, *args, **kwargs):
            raise ValueError("register unavailable")

        with patch(REAL_PURGE, _fake_purge), patch(
                f"{BRIDGE}._privacy_register_destruction", boom):
            self.assertTrue(self.transfer._purge_s3())
        self.assertFalse(self._register_entries())

    def test_actor_is_quoted_in_the_description(self):
        """The trigger the base module logs is carried into the entry."""
        with patch(REAL_PURGE, _fake_purge):
            self.transfer._purge_s3(actor="retention job")
        self.assertIn("retention job", self._register_entries().document_description)

    def test_transfer_models_are_classifiable(self):
        """The bridge opens its models through the extension point."""
        allowed = self.env["privacy.document.classification"]._privacy_classifiable_models()
        self.assertIn("secure.transfer", allowed)
        self.assertIn("secure.transfer.file", allowed)
        # and it did not overwrite those of the base module
        self.assertIn("res.partner", allowed)

    def test_purpose_and_retention_are_declared(self):
        """The purpose and the retention rule are in the register of processing activities."""
        purpose = self.env.ref("bf_securetransfer_privacy.purpose_securetransfer")
        self.assertFalse(purpose.requires_consent)
        calendar = self.env.ref("bf_securetransfer_privacy.retention_securetransfer")
        self.assertEqual(calendar.applies_to_model, "secure.transfer")


@tagged("post_install", "-at_install", "bf_securetransfer_privacy")
class TestRegisterVariants(PurgeCommon):
    """One code for every register release.

    Releases of the register that guard against claiming the deletion of a
    living record offer a partial scope (`destruction_scope`, `res_field`);
    earlier releases have neither field. The bridge declares the scope when the
    register has it and records the entry as before when it does not. Another
    bridge enriches the entry through `_privacy_destruction_vals`, which must
    run BEFORE the entry is created: the register is immutable.
    """

    def _without_scope_fields(self):
        """Removes the two scope fields from the register model, for the
        duration of a `with` block, as on a register release without them."""
        fields_map = type(self.Register)._fields
        ctx = patch.dict(fields_map)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        for fname in ("destruction_scope", "res_field"):
            fields_map.pop(fname, None)

    def test_partial_scope_is_declared_when_the_register_has_it(self):
        """The transfer survives its purge: the entry says only the files left."""
        self.assertIn("destruction_scope", self.Register._fields)
        with patch(REAL_PURGE, _fake_purge):
            self.transfer._purge_s3()
        entry = self._register_entries()
        self.assertEqual(len(entry), 1, "the register's guard accepted the entry")
        self.assertEqual(entry.destruction_scope, "partial")
        self.assertTrue(entry.res_field)
        # The record the entry points to still exists, as the scope says.
        self.assertTrue(self.transfer.exists())

    def test_no_scope_and_no_crash_without_the_fields(self):
        """A register without the scope fields gets an entry without them."""
        captured = []

        def fake_create(this, vals):
            captured.append(dict(vals))
            return this.browse()

        self._without_scope_fields()
        self.assertNotIn("destruction_scope", self.Register._fields)
        vals = self.transfer._privacy_destruction_vals(["file.pdf"])
        self.assertNotIn("destruction_scope", vals)
        self.assertNotIn("res_field", vals)
        # The whole writing path, up to the creation, goes through as well.
        with patch.object(type(self.Register), "create", fake_create):
            self.transfer._privacy_register_destruction(["file.pdf"], subject_count=2)
        self.assertEqual(len(captured), 1)
        self.assertNotIn("destruction_scope", captured[0])
        self.assertNotIn("res_field", captured[0])
        self.assertIn("file.pdf", captured[0]["document_description"])
        self.assertEqual(captured[0]["subject_count"], 2)

    def test_extension_runs_before_the_entry_is_created(self):
        """What an extending bridge adds reaches the immutable entry."""
        original = type(self.env["secure.transfer"])._privacy_destruction_vals
        calls = []

        def extended(this, snapshot, **kwargs):
            calls.append("vals")
            vals = original(this, snapshot, **kwargs)
            vals["document_description"] += "\nAdded by an extending bridge."
            vals["subject_count"] = 7
            return vals

        with patch(REAL_PURGE, _fake_purge), patch.object(
                type(self.env["secure.transfer"]), "_privacy_destruction_vals", extended):
            self.transfer._purge_s3()
        entry = self._register_entries()
        self.assertEqual(calls, ["vals"], "the extension point is called once")
        self.assertEqual(len(entry), 1, "one purge, one entry")
        self.assertIn("Added by an extending bridge.", entry.document_description)
        self.assertEqual(entry.subject_count, 7)

    def test_unnamed_file_is_named_in_the_register(self):
        """A file without a name or key is still counted and named."""
        vals = self.transfer._privacy_destruction_vals(["", "b.pdf"])
        description = vals["document_description"]
        self.assertIn("2 ", description)
        # The blank name is replaced by a placeholder, not left empty.
        self.assertNotRegex(description, r":\s*, b\.pdf")
        self.assertRegex(description, r"\(\w[^)]*\), b\.pdf")
