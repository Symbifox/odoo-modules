{
    "name": "BF Session Cookie Domain",
    "version": "18.0.2.0.0",
    "category": "Tools",
    "summary": "Share the session cookie across the subdomains of one configured apex domain",
    "description": """
        Patches ``odoo.http.set_cookie`` to emit ``domain=.<apex>`` on the
        ``session_id`` cookie, so that the same session is recognised on every
        subdomain of one configured domain.

        The problem it solves: serving Odoo on apex / www. / odoo. through
        separate proxy hosts makes each host a separate cookie scope, so each
        one starts its own OIDC sign-in. Every sign-in rewrites
        ``res_users.oauth_access_token``, which belongs to
        ``_get_session_token_fields()`` — so every other open tab takes a
        SessionExpiredException on its next request.

        ⚠️ Off until configured. Set ``bf_session_cookie_domain.root_domain``
        in ir.config_parameter to the apex domain (``example.com``, no leading
        dot). While it is empty this module changes nothing: a shared session
        cookie widens where a session is valid, so it is opted into rather than
        guessed from the request host.

        The rewrite applies only to requests already on that domain, and only
        to the ``session_id`` key. Other cookies, other domains, and scripted
        access (XML-RPC, webhooks) are untouched.
    """,
    "author"
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["web", "auth_oauth"],
    "auto_install": False,
    "installable": True,
    "application": False,
}
