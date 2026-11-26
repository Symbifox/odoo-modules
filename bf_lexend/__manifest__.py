{
    "name": "Lexend Typeface",
    "summary": "Bundles the Lexend font for Odoo backend, frontend and PDF reports",
    "description": """
        Ships the Lexend typeface, self-hosted, in two static weights: regular
        (400) and semi-bold (600). The backend and the website load it as WOFF2
        (about 37 KB per weight) with the TTF as fallback; PDF reports use the
        TTF (about 100 KB per weight). font-display: swap, no third-party font
        service.

        Once installed, the backend and the website render in Lexend whatever
        the company font is. PDF reports printed with the company document
        layout (invoices, quotations...) keep following res.company.font, to
        which this module adds Lexend as an option; internal and plain reports
        render in Lexend.

        Brand colours and white-label settings are not part of this module:
        install bluefox_branding for them.
    """,
    # 18.0.3.1.0: fr_CA.po traduit le nom et le résumé du module dans
    #   Apps (ir.module.module, identifiant base.module_bf_lexend).
    # 18.0.3.1.1: le backend et le site lisent Lexend en WOFF2 (environ 40 Ko par
    #   graisse au lieu de 100 Ko), le TTF reste en repli et pour les rapports PDF
    #  .
    # 18.0.3.1.2: la description et le README disent ce que fait le module (deux
    #   graisses fixes, Lexend partout dans l'interface et le site dès
    #   l'installation, rapports PDF à la police de la société). Aucun code changé.
    "version": "18.0.3.1.2",
    "category": "Theme/Backend",
    "license": "LGPL-3",
    "author": "Les services de consultation Blue Fox, Inc.",
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
