{
    "name": "AI Bridge (socket transport)",
    "version": "18.0.1.1.0",
    "category": "Technical",
    "summary": "The single transport to the AI bridge service, and the single "
               "parameter saying which tenant is calling",
    "description": """
AI Bridge
=========

Carries the **only** transport to the ``claude-chatbot-bridge`` service, the
**only** system parameter saying where to reach its Unix socket, and the
**only** one saying which tenant is calling.

A deliberately bare leaf module: no view, no menu, no data, and ``base`` as its
sole dependency. That is what lets it be hard-required by modules whose
licences cannot depend on one another, and be installed on a tenant that does
not run the assistant at all.

What it exposes
---------------
* ``env["bf.ai.bridge"].call(endpoint, payload, timeout, headers)``
* ``env["bf.ai.bridge"].stream(endpoint, payload, timeout)``
* ``env["bf.ai.bridge"].socket_path()`` / ``available()`` / ``check_available()``
* ``env["bf.ai.bridge"].tenant()``
* ``bf_ai_bridge.tools.transport.post`` / ``stream`` for the same calls without
  an environment, so a generator can outlive the cursor that created it.

Exceptions are not wrapped: ``socket.timeout``, ``ConnectionRefusedError``,
``FileNotFoundError`` and ``ValueError`` reach the caller as they are.

System parameters
-----------------
``bf_ai_bridge.socket`` (default ``/run/claude-bridge/bridge.sock``, the path as
seen from inside the Odoo container) and ``bf_ai_bridge.tenant`` (**no
default**). On install, the value of an older per-module parameter is carried
over when one exists.

Why the tenant has no default
-----------------------------
A hardcoded default is right for whoever wrote it and wrong everywhere else,
without saying so. A module that announces the wrong tenant to the bridge is
served another customer's data: the call succeeds, and that is the problem.
``tenant()`` raises rather than guess, and every module calling the bridge
reads this parameter instead of hardcoding its own tenant.
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
