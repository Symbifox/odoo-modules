from odoo.fields import Char, Text, Html
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNoPasswordStorage(TransactionCase):
    """The "password is never stored" guarantee is STRUCTURAL: assert no field
    on bf.phishing.result can hold a submitted credential value."""

    def test_no_value_bearing_credential_field(self):
        fields = self.env["bf.phishing.result"]._fields
        # The only credential-related fields allowed are the integer lengths and
        # the boolean flag.
        allowed = {"submitted_username_len", "submitted_password_len",
                   "credentials_submitted"}
        suspicious_tokens = ("password", "passwd", "secret", "credential")
        for name, field in fields.items():
            lname = name.lower()
            if any(tok in lname for tok in suspicious_tokens):
                self.assertIn(
                    name, allowed,
                    "Unexpected credential-related field %r — the schema must "
                    "not be able to store a submitted value." % name)
            # No free-text field should be named like a captured login/password.
            if isinstance(field, (Char, Text, Html)):
                self.assertNotIn(
                    "password", lname,
                    "Text field %r could hold a password value." % name)

    def test_register_submit_stores_only_lengths(self):
        template = self.env["bf.phishing.template"].create({
            "name": "L", "subject": "S", "capture_field_lengths": True})
        partner = self.env["res.partner"].create(
            {"name": "C", "email": "c@example.com"})
        campaign = self.env["bf.phishing.campaign"].create({
            "name": "camp", "template_id": template.id,
            "recipient_partner_ids": [(6, 0, partner.ids)]})
        campaign.action_prepare()
        res = campaign.result_ids[0]

        res.register_submit(username_len=5, password_len=12)
        self.assertTrue(res.credentials_submitted)
        self.assertEqual(res.submitted_username_len, 5)
        self.assertEqual(res.submitted_password_len, 12)
        self.assertEqual(res.state, "submitted")
        # Nowhere is the raw value retrievable — there is no field for it.
        self.assertFalse(
            any("password" in n and not n.endswith("_len")
                for n in res._fields))
