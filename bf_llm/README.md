# Blue Fox — LLM Provider (`bf_llm`)

A single, provider-agnostic LLM gateway for Blue Fox Odoo modules. Instead of
each module hand-rolling its own Anthropic HTTPS calls and plaintext API keys,
they call one service — `bf.llm` — which dispatches to a configured provider
(Anthropic, OpenAI, or any OpenAI-compatible / local server) and returns a
single normalized response envelope.

- **Odoo:** 18.0 CE
- **License:** LGPL-3
- **Author:** Blue Fox Inc.

## What it provides

- `bf.llm` — an `AbstractModel` service with two methods:
  - `chat(messages, system=None, tools=None, max_tokens=None, model=None)`
  - `extract(content_bytes, prompt, schema=None, mime=None, model=None)` for
    document / vision extraction (invoice OCR, business cards, …).
- `bf.llm.provider` — a config model (Settings › Technical › LLM Providers)
  with the provider type, base URL, **Fernet-encrypted** API key, per-feature
  model overrides, capability flags, and a single default provider.
- Ported prompt constants `OCR_PROMPT`, `CARD_PROMPT`, `SIGNATURE_PROMPT_HEADER`
  (importable from `odoo.addons.bf_llm.models.bf_llm`).

## Public API

```python
# Default provider
res = self.env["bf.llm"].default().chat(messages=[{"role": "user", "content": "Hi"}])
if res["error"]:
    ...  # transient / API error — envelope carries the message
else:
    text = res["text"]

# Feature-bound model (uses model_triage / model_ocr / … if set, else the base model)
res = self.env["bf.llm"].for_feature("triage").chat(messages=[...])

# Document / vision extraction (PDF or image bytes — decode base64 first)
import base64
from odoo.addons.bf_llm.models.bf_llm import OCR_PROMPT
res = self.env["bf.llm"].for_feature("ocr").extract(
    base64.b64decode(pdf_b64), OCR_PROMPT, mime="application/pdf"
)
data = res["data"]  # parsed JSON dict, or None with res["error"] set
```

### Response envelope

Every `chat()` / `extract()` returns:

```python
{
    "ok": bool,                 # True iff error is None (and, for extract, JSON parsed)
    "text": str,                # assistant text ("" on error)
    "data": dict | None,        # extract(): parsed JSON; chat(): None
    "tool_calls": [{"id", "name", "input"}],
    "usage": {"input_tokens": int, "output_tokens": int},
    "model": str,
    "provider": "anthropic" | "openai" | "openai_compatible",
    "stop_reason": "end_turn" | "tool_use" | "max_tokens" | "error" | None,
    "degraded": [str],          # capability-fallback notes
    "error": str | None,        # human-readable error; None on success
    "raw": dict | None,         # provider raw JSON (trim before persisting)
}
```

`chat()` / `extract()` **never raise** on a model/API error — they return the
envelope with `error` set. They **do** raise `UserError` for configuration
faults (no default provider, no API key, missing base URL).

The feature → model-override map: `chat`→`model_chat`, `vision`/`extract`→
`model_vision`, `title`→`model_title`, `triage`→`model_triage`, `ocr`→
`model_ocr`. A blank override falls back to the base `model`.

## Configuration

1. Open **Settings › Technical › LLM Providers**.
2. Create (or enable the seeded "Default (Anthropic)") provider, choose the
   provider type, and set the API key. Mark exactly one provider as **default**.
3. For Anthropic, base URL defaults to `https://api.anthropic.com`; for OpenAI,
   `https://api.openai.com/v1`. For an OpenAI-compatible/local server (Ollama,
   LM Studio, vLLM, LiteLLM) set the base URL explicitly
   (e.g. `http://localhost:11434/v1`); the API key may be left blank.

### API key encryption (required)

API keys are stored **Fernet-encrypted** in `ir.config_parameter`, never as a
plaintext column. The encryption key is read from the `BF_LLM_FERNET_KEY`
environment variable, or `bf_llm_fernet_key` in `odoo.conf`. Without a key,
saving an API key raises a clear error and decryption fails closed to `""`.

Generate a key once:

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

## Capability-driven graceful degradation

Each fallback appends a tag to `degraded` and still returns a usable envelope:

- `tools_unsupported` — provider `supports_tools=False`: `tools` dropped, model
  answers in plain text.
- `vision_rasterized` — OpenAI / local + PDF: pages rasterized to PNG before
  upload.
- `ocr_text_prepass` — provider `supports_vision=False`: a Tesseract text
  pre-pass feeds the model as text instead of an image.
- `json_unenforced` — local server can't enforce a JSON schema: falls back to
  prompt-only JSON with lenient parsing.

## Optional system dependencies

These are **soft** — they are import-guarded and not declared in
`external_dependencies` (their absence yields a clean `error` envelope, never an
install-time `ImportError`):

- **`pdf2image` + `poppler-utils`** (system `pdftoppm`) — only needed to send a
  **PDF** to an **OpenAI / OpenAI-compatible** provider (rasterization). The
  Anthropic path ingests PDFs directly and never needs it.
- **`pytesseract` + `tesseract-ocr`** (+ language packs) — only needed when a
  provider has `supports_vision=False` (the OCR text pre-pass).

`cryptography` is the only hard Python dependency.

## Scope (v1)

One-shot request/response only. Streaming and adaptive-thinking are out of scope
for v1 — all current consumers issue single small requests. The default model is
`claude-opus-4-8`; the Anthropic Messages API version header is `2023-06-01`.
