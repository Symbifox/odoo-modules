"""Shared adapter machinery: HTTP POST, JSON leniency, MIME sniff, envelope.

Each provider adapter subclasses _BaseAdapter and implements chat()/extract().
HTTP is stdlib urllib (matching the bf_helpdesk template) — no third-party
client and no provider SDK.
"""
import json
import logging
import urllib.error
import urllib.request

_logger = logging.getLogger(__name__)

# Optional rasterization / OCR dependencies. SOFT — absence yields a clean
# error envelope, never an ImportError at install time.
try:
    from pdf2image import convert_from_bytes
except ImportError:
    convert_from_bytes = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

# Cap on pages rasterized / OCR'd from a multi-page PDF.
MAX_RASTER_PAGES = 8


def parse_json_lenient(text):
    """Parse the first JSON object out of model text.

    Strips ``` fences, tries json.loads, then scans for the first balanced
    {...} span. Returns a dict or None — mirrors the bridge's _run_claude_json.
    """
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        # Drop opening fence (optionally language-tagged) and closing fence.
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = s[start:i + 1]
                    try:
                        obj = json.loads(candidate)
                        return obj if isinstance(obj, dict) else None
                    except Exception:
                        return None
    return None


def sniff_mime(content_bytes):
    """Best-effort MIME sniff from magic bytes. Returns a MIME str or None."""
    if not content_bytes:
        return None
    head = bytes(content_bytes[:16])
    if head[:4] == b"%PDF":
        return "application/pdf"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and bytes(content_bytes[8:12]) == b"WEBP":
        return "image/webp"
    return None


class _BaseAdapter:
    """Base for provider adapters. Holds a reference to the resolved provider."""

    def __init__(self, resolved):
        self.r = resolved
        self.provider_type = resolved.provider.provider

    def empty_envelope(self, model):
        """Return the normalized envelope skeleton (ok=False, error=None)."""
        return {
            "ok": False,
            "text": "",
            "data": None,
            "tool_calls": [],
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "model": model,
            "provider": self.provider_type,
            "stop_reason": None,
            "degraded": [],
            "error": None,
            "raw": None,
        }

    def _post(self, url, headers, body_dict, timeout):
        """POST JSON and return the parsed response dict.

        Raises urllib.error.HTTPError on non-2xx, and URLError/TimeoutError/
        OSError on transport failures, so the caller can map them.
        """
        data = json.dumps(body_dict).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)
