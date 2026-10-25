{
    "name": "Symbifox — Blue Fox OS Policy, Live USB Key",
    "version": "18.0.1.3.0",
    "category": "Tools",
    "summary": "Key escrow and one-time Odoo sign-in for the Blue Fox OS "
               "live USB key",
    "description": """
Symbifox — Blue Fox OS Policy, Live USB Key
===========================================
The Blue Fox OS live USB key opens a Linux session with a QR code (OAuth2
device flow, dedicated public client). The person chooses an ephemeral session
or a persistent profile: one LUKS2 file per person, on the key.

The key that decrypts that profile is never on the USB key and never typed: it
is escrowed here, encrypted like bf_policy's disk passphrases, and served after
the QR code by POST /api/v1/policy/live/profile-key. Revoking a profile erases
the escrowed key: the encrypted profile left on the USB key becomes unreadable.
That is the remote wipe.

With the same QR token, the live key also obtains a one-time Odoo sign-in link
(POST /api/v1/policy/live/login-link, valid 5 minutes): opened in the key's
browser (/odoo?bfos_login=...), it opens the person's Odoo session without
typing anything or asking again for the second factor, already done in
Authentik with the QR code. Since 18.0.1.3.0, Odoo itself requires `mfa` in
the token's `amr` claim before serving a profile key or issuing that link, and
a link only opens on a navigation started by the browser itself, never in a
browser already signed in as another user.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_policy", "web"],
    "data": [
        "security/ir.model.access.csv",
        "views/bf_policy_live_views.xml",
    ],
    "auto_install": False,
    "installable": True,
    "application": False,
}
