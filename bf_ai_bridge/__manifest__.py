{
    "name": "AI Bridge (socket transport)",
    "version": "18.0.1.0.1",
    "category": "Technical",
    "summary": "The single transport to the AI bridge service",
    "description": """
AI Bridge
=========

Carries the **only** transport to the ``claude-chatbot-bridge`` service, and
the **only** system parameter saying where to reach its Unix socket.

A deliberately bare leaf module: no view, no menu, no data, and ``base`` as its
sole dependency. That is what lets it be hard-required by modules whose
licences cannot depend on one another, and be installed on a tenant that does
not run the assistant at all.

What it exposes
---------------
* ``env["bf.ai.bridge"].call(endpoint, payload, timeout, headers)``
* ``env["bf.ai.bridge"].stream(endpoint, payload, timeout)``
* ``env["bf.ai.bridge"].socket_path()`` / ``available()`` / ``check_available()``
* ``bf_ai_bridge.tools.transport.post`` / ``stream`` for the same calls without
  an environment, so a generator can outlive the cursor that created it.

Exceptions are not wrapped: ``socket.timeout``, ``ConnectionRefusedError``,
``FileNotFoundError`` and ``ValueError`` reach the caller as they are.

System parameter
----------------
``bf_ai_bridge.socket`` (default ``/run/claude-bridge/bridge.sock``, the path as
seen from inside the Odoo container). On install, the value of an older
per-module parameter is carried over when one exists.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["base"],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
