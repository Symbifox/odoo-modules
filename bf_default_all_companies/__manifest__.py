{
    "name": "BF Default All Companies",
    "version": "18.0.1.0.0",
    "category": "Tools",
    "summary": "Pre-select every allowed company in the switcher when no cids cookie is set",
    "description": """
        On webclient bootstrap, if the browser has no `cids` cookie (first login,
        post-logout, or cookie expired), pre-populate it with every company in
        the user's `company_ids`. The standard companyService then picks up the
        full list and shows them all toggled on.

        User manual choices via the switcher still win for the rest of the
        session: the cookie is only seeded when missing.

        Single-company users are unaffected (the seed is a no-op when
        allowed_companies has one entry).
    """,
    "author": "Blue Fox Inc.",
    "website": "https://bluefoxconsultant.com",
    'license': 'LGPL-3',
    "depends": ["web"],
    "assets": {
        "web.assets_backend": [
            "bf_default_all_companies/static/src/preset_cids.js",
        ],
    },
    "auto_install": False,
    "installable": True,
    "application": False,
}
