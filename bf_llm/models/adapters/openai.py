"""OpenAI Chat Completions adapter (also serves openai_compatible / local).

POST {base_url}/chat/completions with Authorization: Bearer. OpenAI cannot read
PDF blocks, so extract() rasterizes PDFs to PNG (pdf2image + poppler) when the
dependency is present; otherwise it returns a clean error envelope.
"""
import base64
import io
import json
import urllib.error

from odoo import _
from odoo.exceptions import UserError

from . import base as _base
from .base import _BaseAdapter


class OpenAIAdapter(_BaseAdapter):

    def chat(self, messages, system, tools, max_tokens, model, degraded):
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(messages)
        return self._request(model, oai_messages, max_tokens, degraded, tools=tools)

    def extract(self, content_bytes, prompt, schema, mime, model, max_tokens, degraded):
        parts = [{"type": "text", "text": prompt}]
        if mime == "application/pdf":
            if _base.convert_from_bytes is None:
                env = self.empty_envelope(model)
                env["degraded"] = degraded
                env["error"] = _(
                    "PDF rasterization indisponible (installer pdf2image + poppler) "
                    "ou utiliser un fournisseur Anthropic"
                )
                env["stop_reason"] = "error"
                return env
            try:
                images = _base.convert_from_bytes(
                    content_bytes, fmt="png", first_page=1, last_page=_base.MAX_RASTER_PAGES
                )
            except Exception as e:
                env = self.empty_envelope(model)
                env["degraded"] = degraded
                env["error"] = _("Echec de la rasterisation du PDF : %s") % e
                env["stop_reason"] = "error"
                return env
            for img in images[: _base.MAX_RASTER_PAGES]:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + b64},
                    }
                )
            degraded.append("vision_rasterized")
        else:
            b64 = base64.b64encode(content_bytes).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:%s;base64,%s" % (mime or "image/png", b64)
                    },
                }
            )
        if schema and self.r.provider.supports_tools:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "extract", "schema": schema, "strict": True},
            }
        else:
            response_format = {"type": "json_object"}
            if schema:
                degraded.append("json_unenforced")
        return self._request(
            model,
            [{"role": "user", "content": parts}],
            max_tokens,
            degraded,
            response_format=response_format,
        )

    # ------------------------------------------------------------------

    def _request(self, model, oai_messages, max_tokens, degraded, tools=None, response_format=None):
        env = self.empty_envelope(model)
        env["degraded"] = degraded
        key = self.r.provider._decrypt_key()
        if not key and self.provider_type == "openai":
            raise UserError(
                _("Aucune cle API n'est configuree pour le fournisseur LLM « %s ».")
                % self.r.provider.name
            )
        base = self.r.provider._effective_base_url()
        body = {"model": model, "max_tokens": max_tokens, "messages": oai_messages}
        if tools:
            body["tools"] = [self._translate_tool(t) for t in tools]
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format
        headers = {"content-type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        url = base.rstrip("/") + "/chat/completions"
        try:
            data = self._post(url, headers, body, self.r.provider.timeout or 60)
        except urllib.error.HTTPError as e:
            env["error"] = self._http_error_msg(e)
            env["stop_reason"] = "error"
            return env
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            env["error"] = _("OpenAI API injoignable : %s") % e
            env["stop_reason"] = "error"
            return env
        self._parse_into(env, data)
        return env

    @staticmethod
    def _translate_tool(tool):
        """Anthropic-shape tool → OpenAI function-tool shape."""
        return {
            "type": "function",
            "function": {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object"},
            },
        }

    def _parse_into(self, env, data):
        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices else {}) or {}
        env["text"] = (message.get("content") or "").strip()
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                parsed = json.loads(fn.get("arguments") or "{}")
            except Exception:
                parsed = {}
            tool_calls.append(
                {"id": tc.get("id"), "name": fn.get("name"), "input": parsed}
            )
        env["tool_calls"] = tool_calls
        usage = data.get("usage") or {}
        env["usage"] = {
            "input_tokens": usage.get("prompt_tokens", 0) or 0,
            "output_tokens": usage.get("completion_tokens", 0) or 0,
        }
        finish = choices[0].get("finish_reason") if choices else None
        env["stop_reason"] = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }.get(finish, "end_turn")
        env["raw"] = data
        env["ok"] = True
        env["error"] = None

    def _http_error_msg(self, e):
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        msg = None
        try:
            payload = json.loads(body)
            msg = (payload.get("error") or {}).get("message")
        except Exception:
            pass
        if msg:
            return "OpenAI API HTTP %s: %s" % (e.code, msg)
        return "OpenAI API HTTP %s: %s" % (e.code, body[:200])
