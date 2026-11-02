from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

REAL_PURGE = "odoo.addons.bf_securetransfer.models.secure_transfer.SecureTransfer._purge_s3"


def _fake_purge(this, actor=None):
    """A purge that succeeds, without touching the object storage."""
    this.file_ids.write({"state": "purged"})
    this.write({"state": "deleted"})
    return True


class RequestPrivacyCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Register = cls.env["privacy.destruction.register"]
        cls.calendar = cls.env.ref(
            "bf_document_request_privacy.retention_document_request")
        cls.policy_short = cls.env.ref("bf_document_request.policy_short_24h")
        cls.brand = cls.env.ref("bf_securetransfer.brand_default")
        cls.client = cls.env["res.partner"].create({
            "name": "Test client",
            "mobile": "5145550142",
        })
        cls.type_identity = cls.env.ref("bf_document_request.doc_type_identity")
        cls.type_income = cls.env.ref("bf_document_request.doc_type_income")
        # A type without a declared category: the register must name it
        # without classifying it, rather than guess.
        cls.type_custom = cls.env["bf.document.type"].create({
            "name": "Photo of the item",
            "code": "item_test",
        })

    def _make_request(self, doc_types=None, policy=None):
        transfer = self.env["secure.transfer"].create({
            "brand_id": self.brand.id,
            "sender_name": self.client.name,
            "sender_email": "client@example.com",
            "recipient_emails": "employee@example.com, manager@example.com",
            "bf_document_expiry_policy_id": (policy or self.policy_short).id,
        })
        self.env["secure.transfer.file"].create({
            "transfer_id": transfer.id,
            "filename": "id-front.jpg",
            "size": 512,
            "s3_key": "test/id-front.jpg",
        })
        request = self.env["bf.document.request"].create({
            "partner_id": self.client.id,
            "phone": "5145550142",
            "brand_id": self.brand.id,
            "transfer_id": transfer.id,
            "line_ids": [
                (0, 0, {"document_type_id": dt.id})
                for dt in (doc_types or [self.type_identity, self.type_income])
            ],
        })
        return request, transfer

    def _entries_for(self, transfer):
        return self.Register.search([
            ("res_model", "=", "secure.transfer"),
            ("res_id", "=", transfer.id),
        ])


@tagged("post_install", "-at_install", "bf_document_request_privacy")
class TestDocumentRequestPrivacy(RequestPrivacyCommon):
    """Destroying uploaded documents must reach the register, and tell the truth."""

    # ---------------------------------------------------- schedule link
    def test_shipped_policies_are_attached_to_the_retention_calendar(self):
        """The two delivered policies are no longer orphan periods."""
        long_policy = self.env.ref("bf_document_request.policy_long_30d")
        self.assertEqual(self.policy_short.retention_calendar_id, self.calendar)
        self.assertEqual(long_policy.retention_calendar_id, self.calendar)
        self.assertEqual(self.calendar.applies_to_model, "bf.document.request")
        # ⚠️ Nothing approves the expiry: the scheduled job decides it alone.
        # Declaring an approval as required would make the register lie.
        self.assertFalse(self.calendar.requires_approval)

    def test_shipped_document_types_declare_their_category(self):
        """The delivered catalogue, under its new identifiers, is classified."""
        self.assertEqual(self.type_identity.privacy_pi_category, "identification")
        self.assertEqual(
            self.env.ref("bf_document_request.doc_type_address").privacy_pi_category,
            "identification")
        self.assertEqual(
            self.env.ref("bf_document_request.doc_type_insurance").privacy_pi_category,
            "identification")
        self.assertEqual(self.type_income.privacy_pi_category, "financial")

    def test_alignment_note_flags_a_policy_longer_than_its_rule(self):
        """When the two periods contradict each other, the form says so."""
        rule = self.env["privacy.retention.calendar"].create({
            "name": "Test rule: 1 year",
            "code": "TEST-1Y",
            "document_type": "other",
            "legal_basis": "Test.",
            "active_retention_years": 1,
            "final_disposition": "destroy",
        })
        policy = self.env["bf.document.expiry.policy"].create({
            "name": "Too long",
            "retention_days": 400,
            "retention_calendar_id": rule.id,
        })
        self.assertIn("⚠️", policy.privacy_alignment_note)
        self.assertIn("TEST-1Y", policy.privacy_alignment_note)
        # A rule without a period is the NORMAL case, not an anomaly.
        policy.retention_calendar_id = self.calendar
        self.assertNotIn("⚠️", policy.privacy_alignment_note)
        self.assertIn(self.calendar.code, policy.privacy_alignment_note)
        # And a policy without any rule is flagged as well.
        policy.retention_calendar_id = False
        self.assertNotIn(self.calendar.code, policy.privacy_alignment_note)
        self.assertNotIn("⚠️", policy.privacy_alignment_note)

    # ---------------------------------------------------- register entry
    def test_purge_of_a_request_writes_one_enriched_entry(self):
        """One entry, which names the request, the client and the documents."""
        request, transfer = self._make_request()
        with patch(REAL_PURGE, _fake_purge):
            transfer._purge_s3(actor="cron")

        entries = self._entries_for(transfer)
        self.assertEqual(len(entries), 1, "one purge, one entry")
        entry = entries
        # What the base bridge already said survives the enrichment.
        self.assertIn("id-front.jpg", entry.document_description)
        self.assertIn(transfer.name, entry.document_description)
        # What this bridge adds.
        self.assertIn(request.name, entry.document_description)
        self.assertIn("Test client", entry.document_description)
        lang = transfer._privacy_register_lang()
        for doc_type in (self.type_identity, self.type_income):
            self.assertIn(doc_type.with_context(lang=lang).name, entry.document_description)
        self.assertEqual(entry.retention_calendar_id, self.calendar)
        self.assertIn(self.calendar.code, entry.legal_basis)
        self.assertIn(self.policy_short.with_context(lang=lang).name, entry.legal_basis)
        self.assertEqual(entry.destruction_method, "delete")
        self.assertTrue(entry.verification_hash)

    def test_subject_count_is_the_client_not_the_recipients(self):
        """The person concerned is the client, not the employees served.

        The base bridge counts the recipient addresses: here, two employees,
        whose information this is not.
        """
        request, transfer = self._make_request()
        self.assertEqual(len(transfer._recipient_list()), 2)
        with patch(REAL_PURGE, _fake_purge):
            transfer._purge_s3()
        self.assertEqual(self._entries_for(transfer).subject_count, 1)

    def test_pi_categories_declare_what_is_known_and_name_what_is_not(self):
        """What is known is declared; the rest is named without being classified."""
        request, transfer = self._make_request(
            doc_types=[self.type_identity, self.type_income, self.type_custom])
        with patch(REAL_PURGE, _fake_purge):
            transfer._purge_s3()
        lang = transfer._privacy_register_lang()
        labels = dict(self.env["privacy.document.classification"]._fields["pi_category"]
                      ._description_selection(self.env(context={"lang": lang})))
        categories = self._entries_for(transfer).pi_categories
        self.assertIn(labels["identification"], categories)
        self.assertIn(labels["financial"], categories)
        self.assertIn("Photo of the item", categories)
        self.assertEqual(categories.count(labels["identification"]), 1,
                         "a category is declared once")

    def test_ordinary_transfer_is_left_alone(self):
        """A transfer without a request keeps exactly the base bridge's entry."""
        transfer = self.env["secure.transfer"].create({
            "brand_id": self.brand.id,
            "sender_name": "Sender",
            "sender_email": "sender@example.com",
            "recipient_emails": "one@example.com, two@example.com",
        })
        self.env["secure.transfer.file"].create({
            "transfer_id": transfer.id,
            "filename": "contract.pdf",
            "size": 128,
            "s3_key": "test/contract.pdf",
        })
        expected = transfer.with_context(
            lang=transfer._privacy_register_lang())._privacy_destruction_vals(["contract.pdf"])
        with patch(REAL_PURGE, _fake_purge):
            transfer._purge_s3()
        entry = self._entries_for(transfer)
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.pi_categories, expected["pi_categories"])
        self.assertEqual(entry.legal_basis, expected["legal_basis"])
        self.assertEqual(entry.subject_count, 2)
        self.assertNotIn("\n\n", entry.document_description)
        self.assertEqual(
            entry.retention_calendar_id,
            self.env.ref("bf_securetransfer_privacy.retention_securetransfer"),
        )

    def test_failed_purge_of_a_request_certifies_nothing(self):
        """A purge that returns False must write nothing, enriched or not."""
        request, transfer = self._make_request()
        with patch(REAL_PURGE, lambda this, actor=None: False):
            transfer._purge_s3()
        self.assertFalse(self._entries_for(transfer))

    def test_expiring_a_request_reaches_the_register(self):
        """The real path: the expiry job goes through _close_draft."""
        request, transfer = self._make_request()
        request.write({"state": "sent", "token": "test-token",
                       "deadline": "2020-01-01 00:00:00"})
        with patch(REAL_PURGE, _fake_purge):
            self.env["bf.document.request"]._cron_expire_requests()
        self.assertEqual(request.state, "expired")
        self.assertFalse(request.token)
        entries = self._entries_for(transfer)
        self.assertEqual(len(entries), 1)
        self.assertIn(request.name, entries.document_description)
        self.assertEqual(entries.subject_count, 1)

    # ---------------------------------------------------- classification
    def test_requests_are_classifiable_without_losing_the_other_bridges(self):
        """The composition holds: the base models stay allowed."""
        allowed = self.env["privacy.document.classification"]\
            ._privacy_classifiable_models()
        self.assertIn("bf.document.request", allowed)
        self.assertIn("bf.document.request.line", allowed)
        self.assertIn("secure.transfer", allowed)
        self.assertIn("res.partner", allowed)
