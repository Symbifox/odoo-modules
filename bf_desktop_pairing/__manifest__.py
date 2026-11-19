{
    "name": "Symbifox Desktop Pairing",
    # 18.0.1.0.0 : pairing layer for DESKTOP surfaces (Plasma widgets on
    #   Blue Fox OS, and whatever follows). One row per installation, a bearer
    #   token recognised by its hash, and a device authorization flow
    #   (RFC 8628): the application needs no embedded browser, no URL scheme
    #   and no local port. It asks for a code, opens the workstation's browser
    #   on a page that requires the Odoo session (password, SSO, TOTP), then
    #   polls until approval. The token NEVER travels in a URL.
    "version": "18.0.1.0.0",
    "category": "Technical",
    "summary": "Device pairing for Blue Fox OS desktop widgets",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    # Deliberately bare: a pairing layer must not pull in the surface that
    # uses it. The modules that accept these tokens (bf_bloc_notes) do not
    # depend on it either: they recognise the model if it is in the registry,
    # and do without it otherwise.
    "depends": ["base", "web"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_desktop_device_security.xml",
        "data/bf_desktop_cron.xml",
        "views/bf_desktop_device_views.xml",
    ],
    "installable": True,
    "application": False,
}
