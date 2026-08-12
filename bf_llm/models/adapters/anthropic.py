"""Anthropic Messages API adapter.

POST {base_url}/v1/messages with x-api-key + anthropic-version headers.
base64 PDF/image content blocks are ingested directly — no rasterization.
"""
import base64
import urllib.error

from odoo import _
from odoo.exceptions import UserError

from .base import _BaseAdapter

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter(_BaseAdapter):

    def chat(self, messages, system, tools, max_tokens, model, degraded):
        env = self.empty_envelope(model)
        env["degraded"] = degraded
        key = self.r.provider._decrypt_key()
        if not key:
            raise UserError(
                _("Aucune cle API n'est configuree pour le fournisseur LLM « %s ».")
                % self.r.provider.name
            )
        base = self.r.provider._effective_base_url()
        body = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools
        headers = {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        url = base.rstrip("/") + "/v1/messages"
        try:
            data = self._post(url, headers, body, self.r.provider.timeout or 60)
        except urllib.error.HTTPError as e:
            env["error"] = self._http_error_msg(e)
            env["stop_reason"] = "error"
            return env
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            env["error"] = _("Anthropic API injoignable : %s") % e
            env["stop_reason"] = "error"
            return env
        self._parse_into(env, data)
        return env

    def extract(self, content_bytes, prompt, schema, mime, model, max_tokens, degraded):
        b64 = base64.b64encode(content_bytes).decode("ascii")
        if mime == "application/pdf":
            doc_block = {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64,
                },
            }
        else:
            doc_block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime or "image/png",
                    "data": b64,
                },
            }
        # schema is recorded but not enforced for Anthropic in v1 (prompt-only).
        messages = [
            {"role": "user", "content": [doc_block, {"type": "text", "text": prompt}]}
        ]
        return self.chat(messages, None, None, max_tokens, model, degraded)

    # ------------------------------------------------------------------

    def _parse_into(self, env, data):
        content = data.get("content") or []
        texts = []
        tool_calls = []
        for block in content:
            btype = block.get("type")
            if btype == "text":
                texts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input") or {},
                    }
                )
        env["text"] = "\n".join(texts).strip()
        env["tool_calls"] = tool_calls
        usage = data.get("usage") or {}
        env["usage"] = {
            "input_tokens": usage.get("input_tokens", 0) or 0,
            "output_tokens": usage.get("output_tokens", 0) or 0,
        }
        # Pass Anthropic's stop_reason through as-is (incl. refusal / stop_sequence)
        # so consumers can branch; only default when absent.
        env["stop_reason"] = data.get("stop_reason") or "end_turn"
        env["raw"] = data
        env["ok"] = True
        env["error"] = None

    def _http_error_msg(self, e):
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return "Anthropic API HTTP %s: %s" % (e.code, body[:200])
