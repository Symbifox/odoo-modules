{
    "name": "Lexend Typeface",
    "summary": "Bundles the Lexend font for Odoo backend, frontend and PDF reports",
    "description": """
        Loads the Lexend variable font and adds it as a selectable option on
        res.company.font. Brand color fields and the Settings UI for them live in
        `bluefox_branding` since 18.0.3.0.0 — install bluefox_branding to manage
        brand colours and white-label settings.

        Lexend CSS is loaded globally; tenants that pick a different font via
        res.company.font will still have the asset present (~120KB total, self-hosted,
        font-display: swap) but the rest of the UI/PDFs will render in the chosen font.
    """,
    "version": "18.0.3.0.0",
    "category": "Theme/Backend",
    "license": "LGPL-3",
    "author": "Les services de consultation Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "depends": ["web"],
    "data": [],
    "assets": {
        "web.assets_backend": ["bf_lexend/static/src/scss/lexend.css"],
        "web.assets_frontend": ["bf_lexend/static/src/scss/lexend.css"],
        "web.report_assets_common": ["bf_lexend/static/src/scss/lexend_report.css"],
        "web.report_assets_pdf": ["bf_lexend/static/src/scss/lexend_report.css"],
    },
}
