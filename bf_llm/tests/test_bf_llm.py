import os
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

_POST = "odoo.addons.bf_llm.models.adapters.base._BaseAdapter._post"


class TestBfLlm(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if Fernet is not None:
            os.environ["BF_LLM_FERNET_KEY"] = Fernet.generate_key().decode()
        # The seed default provider is disabled; create an enabled default for tests.
        cls.env["bf.llm.provider"].search([]).write({"is_default": False})
        cls.provider = cls.env["bf.llm.provider"].create(
            {
                "name": "Test Anthropic",
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "model_triage": "claude-haiku-4-5",
                "is_default": True,
                "enabled": True,
            }
        )

    # ------------------------------------------------------------------
    # Fernet round-trip
    # ------------------------------------------------------------------

    def test_fernet_round_trip(self):
        if Fernet is None:
            self.skipTest("cryptography not installed")
        self.provider.write({"api_key": "sk-test-12345"})
        # Plaintext must NOT be stored on the record column.
        self.provider.invalidate_recordset(["api_key"])
        self.assertEqual(self.provider.api_key, "sk-test-12345")
        self.assertEqual(self.provider._decrypt_key(), "sk-test-12345")
        # The stored param is ciphertext, not the plaintext.
        param = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(self.provider._api_key_param())
        )
        self.assertTrue(param)
        self.assertNotIn("sk-test-12345", param)

    # ------------------------------------------------------------------
    # default() / for_feature() resolution
    # ------------------------------------------------------------------

    def test_default_requires_configured_provider(self):
        self.provider.write({"is_default": False})
        with self.assertRaises(UserError):
            self.env["bf.llm"].default()

    def test_for_feature_binds_model(self):
        resolved = self.env["bf.llm"].for_feature("triage")
        self.assertEqual(resolved._resolve_model(None), "claude-haiku-4-5")
        # A feature with no override falls back to the base model.
        self.assertEqual(
            self.env["bf.llm"].for_feature("chat")._resolve_model(None),
            "claude-opus-4-8",
        )

    def test_single_default_enforced(self):
        second = self.env["bf.llm.provider"].create(
            {
                "name": "Second",
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "is_default": True,
                "enabled": True,
            }
        )
        self.provider.invalidate_recordset(["is_default"])
        self.assertTrue(second.is_default)
        self.assertFalse(self.provider.is_default)

    # ------------------------------------------------------------------
    # Envelope normalization (mocked HTTP)
    # ------------------------------------------------------------------

    def _canned_anthropic(self, text="hello"):
        return {
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "stop_reason": "end_turn",
        }

    def test_chat_envelope_shape(self):
        if Fernet is None:
            self.skipTest("cryptography not installed")
        self.provider.write({"api_key": "sk-x"})
        with patch(_POST, return_value=self._canned_anthropic()):
            res = self.env["bf.llm"].default().chat(
                [{"role": "user", "content": "hi"}]
            )
        for key in (
            "ok", "text", "data", "tool_calls", "usage", "model",
            "provider", "stop_reason", "degraded", "error", "raw",
        ):
            self.assertIn(key, res)
        self.assertTrue(res["ok"])
        self.assertEqual(res["text"], "hello")
        self.assertEqual(res["provider"], "anthropic")
        self.assertEqual(res["model"], "claude-opus-4-8")
        self.assertEqual(res["usage"]["input_tokens"], 5)
        self.assertEqual(res["stop_reason"], "end_turn")
        self.assertIsNone(res["error"])
        self.assertEqual(res["data"], None)

    def test_extract_parses_json(self):
        if Fernet is None:
            self.skipTest("cryptography not installed")
        self.provider.write({"api_key": "sk-x"})
        canned = self._canned_anthropic(text='```json\n{"vendor_name": "ACME"}\n```')
        pdf = b"%PDF-1.4 fake"
        with patch(_POST, return_value=canned):
            res = self.env["bf.llm"].for_feature("ocr").extract(
                pdf, "extract", mime="application/pdf"
            )
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"], {"vendor_name": "ACME"})

    def test_extract_invalid_json(self):
        if Fernet is None:
            self.skipTest("cryptography not installed")
        self.provider.write({"api_key": "sk-x"})
        canned = self._canned_anthropic(text="not json at all")
        with patch(_POST, return_value=canned):
            res = self.env["bf.llm"].default().extract(
                b"%PDF-1.4", "extract", mime="application/pdf"
            )
        self.assertFalse(res["ok"])
        self.assertIsNone(res["data"])
        self.assertEqual(res["error"], "LLM did not return valid JSON")
        # Raw text is still returned so the caller can persist it.
        self.assertEqual(res["text"], "not json at all")

    # ------------------------------------------------------------------
    # Capability gating
    # ------------------------------------------------------------------

    def test_tools_degraded_when_unsupported(self):
        if Fernet is None:
            self.skipTest("cryptography not installed")
        self.provider.write({"api_key": "sk-x", "supports_tools": False})
        tool = {"name": "t", "description": "d", "input_schema": {"type": "object"}}
        with patch(_POST, return_value=self._canned_anthropic()) as mocked:
            res = self.env["bf.llm"].default().chat(
                [{"role": "user", "content": "hi"}], tools=[tool]
            )
        self.assertIn("tools_unsupported", res["degraded"])
        # tools must have been dropped from the outbound body.
        sent_body = mocked.call_args.args[2]
        self.assertNotIn("tools", sent_body)

    def test_no_vision_without_tesseract_fails_closed(self):
        if Fernet is None:
            self.skipTest("cryptography not installed")
        self.provider.write({"api_key": "sk-x", "supports_vision": False})
        with patch("odoo.addons.bf_llm.models.bf_llm._base.pytesseract", None):
            res = self.env["bf.llm"].default().extract(
                b"%PDF-1.4", "extract", mime="application/pdf"
            )
        self.assertFalse(res["ok"])
        self.assertEqual(res["stop_reason"], "error")
        self.assertTrue(res["error"])

    # ------------------------------------------------------------------
    # Lenient JSON parser helper
    # ------------------------------------------------------------------

    def test_parse_json_helper(self):
        BfLlm = self.env["bf.llm"]
        self.assertEqual(BfLlm._parse_json('{"a": 1}'), {"a": 1})
        self.assertEqual(BfLlm._parse_json('prefix {"a": 1} suffix'), {"a": 1})
        self.assertEqual(BfLlm._parse_json("```json\n{\"a\": 1}\n```"), {"a": 1})
        self.assertIsNone(BfLlm._parse_json("no json here"))
