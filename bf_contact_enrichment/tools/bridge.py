"""Thin client for the GenFox bridge Unix socket.

Uses the established Blue Fox bridge pattern: a hand-rolled HTTP/1.1 request
over an ``AF_UNIX`` stream socket, so we depend on nothing beyond the
standard library inside the Odoo container. The bridge wraps ``claude -p``.

As of 18.0.1.2.0 only the **company/domain** enrichment path uses this shim
(``/enrich/company`` — agentic web research, out of scope for bf_llm v1). The
business-card and signature paths now go through the ``bf.llm`` gateway.
"""
import json
import socket

_DEFAULT_SOCKET = "/run/claude-bridge/bridge.sock"


def _raw_call(endpoint, payload, socket_path, timeout):
    body = json.dumps(payload).encode()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        req = (
            f"POST {endpoint} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode() + body
        sock.sendall(req)

        chunks = []
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).decode()

        header_end = raw.find("\r\n\r\n")
        if header_end == -1:
            raise ValueError("Malformed HTTP response from bridge")
        status_line = raw[:raw.find("\r\n")]
        status_code = int(status_line.split(" ", 2)[1])
        resp_body = raw[header_end + 4:]

        # Handle chunked transfer encoding
        headers_block = raw[:header_end].lower()
        if "transfer-encoding: chunked" in headers_block:
            decoded = []
            pos = 0
            while pos < len(resp_body):
                nl = resp_body.find("\r\n", pos)
                if nl == -1:
                    break
                size = int(resp_body[pos:nl], 16)
                if size == 0:
                    break
                decoded.append(resp_body[nl + 2:nl + 2 + size])
                pos = nl + 2 + size + 2
            resp_body = "".join(decoded)

        if status_code >= 400:
            raise ValueError(f"Bridge HTTP {status_code}: {resp_body[:200]}")
        return json.loads(resp_body)
    finally:
        sock.close()


def call_bridge(env, endpoint, payload, timeout=100):
    """POST to a bridge endpoint, reading the socket path from Odoo config.

    The socket path is configurable via the ``bf_claude_chat.bridge_socket``
    system parameter (shared with bf_invoice_ocr / bf_claude_chat).
    """
    socket_path = env["ir.config_parameter"].sudo().get_param(
        "bf_claude_chat.bridge_socket", _DEFAULT_SOCKET
    )
    return _raw_call(endpoint, payload, socket_path, timeout)
