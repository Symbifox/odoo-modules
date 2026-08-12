{
    "name": "Symbifox Branding",
    "version": "18.0.3.4.0",
    "category": "Tools",
    "summary": "White-label branding panel + branded email templates",
    "description": """
        Branding module — owns the white-label fields on res.company and the
        Settings UI (Paramètres → Général → Identité de marque) that lets any
        tenant rebrand the instance without editing modules.

        Exposed fields:
        - Logo + favicon (standard res.company fields, surfaced in Settings)
        - Primary + dark brand colors (used by navbar, buttons, branded emails, PDF reports)
        - Font selection (Lexend default, swappable per company)
        - Branded email tagline / custom footer HTML / default signature

        Also ships:
        - Branded transactional mail layout (bf_mail_layout) reading all of the above
        - Branded payment followup, contract, helpdesk, survey, calendar templates (French)
        - Late invoice notice template branding (post_init_hook)
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    'license': 'Other proprietary',
    "depends": [
        "web",
        "mail",
        "account",
        "sale",
        "calendar",
        "om_account_followup",
        "contract",
        "helpdesk_mgmt",
        "survey",
        "portal",  # for website_brand_css_variables.xml inheriting portal.frontend_layout
        "bf_lexend",  # provides the Lexend font assets + ("Lexend", "Lexend") selection_add on res.company.font
        "bf_onboarding_base",
        "l10n_ca",  # for views/report_layout_overrides.xml inheriting l10n_ca_external_layout_folder
    ],
    "data": [
        "data/mail_layout_override.xml",
        "data/bf_onboarding.xml",
        "views/res_config_settings_views.xml",
        "views/brand_css_variables.xml",
        "views/website_brand_css_variables.xml",
        "views/report_layout_overrides.xml",
        # mail_template_overrides.xml is NOT loaded by Odoo data loader
        # (original templates have noupdate=True). Instead, post_init_hook
        # reads this file and applies updates via ORM write().
    ],
    "assets": {
        "web._assets_primary_variables": [
            ("prepend", "bluefox_branding/static/src/scss/primary_variables.scss"),
        ],
        "web.assets_backend": [
            "bluefox_branding/static/src/scss/branding.scss",
        ],
        "web.assets_frontend": [
            "bluefox_branding/static/src/scss/branding.scss",
        ],
        # Make HTML-editor embedded file pills render in PDF reports.
        # See report_embedded_files.scss for the why.
        "web.report_assets_common": [
            "bluefox_branding/static/src/scss/report_embedded_files.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}

