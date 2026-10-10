{
    "name": "Symbifox Branding",
    # 18.0.3.26.0: les courriels système prennent la marque. Le gabarit
    #   « light » d'Odoo (avis de sécurité, code et invitation 2FA, rappel des comptes non
    #   activés, partage de projet, sondages, évaluations…) est habillé par une vue
    #   d'extension, car aucun mail.template ne l'atteint ; l'avis de sécurité est réécrit
    #   (« Bonjour », lien 2FA vers /my/security, courriel de la société), la
    #   réinitialisation du mot de passe et l'alerte de nouvel appareil (vues QWeb
    #   autoportées) aussi ; quatre gabarits d'accès rejoignent les surcharges, sauf
    #   retouche à la main du locataire, et l'invitation, l'avis de changement et le rappel
    #   du calendrier perdent leur coquille (ils passent par le gabarit light : carte dans
    #   la carte). Textes des vues en anglais, français dans i18n/fr_CA.po. La migration
    #   n'écrit que ces sept gabarits, jamais les autres surcharges.
    "version": "18.0.3.26.0",
    "category": "Tools",
    "summary": "White-label branding panel + branded email templates",
    "description": """
        Branding module — owns the white-label fields on res.company and the
        Settings UI (Paramètres → Général → Identité de marque) that lets any
        tenant rebrand the instance without editing modules.

        Exposed fields:
        - Logo + favicon (browser tab, iOS home screen and PWA manifest icons)
        - Primary + dark brand colors (used by navbar, buttons, branded emails, PDF reports)
        - Font selection (Lexend default, swappable per company)
        - Branded email tagline / custom footer HTML / default signature

        Also ships:
        - Branded transactional mail layout (bf_mail_layout) reading all of the above
        - Odoo's light layout, security notice, password reset and new device alert in the brand
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
        "auth_signup",  # data/auth_mail_overrides.xml (password reset, new device alert); portal needs it anyway
        "bf_lexend",  # provides the Lexend font assets + ("Lexend", "Lexend") selection_add on res.company.font
        "bf_onboarding_base",
        "l10n_ca",  # for views/report_layout_overrides.xml inheriting l10n_ca_external_layout_folder
    ],
    "data": [
        "data/mail_layout_override.xml",
        "data/mail_layout_relais.xml",
        "data/mail_layout_light.xml",
        "data/auth_mail_overrides.xml",
        "data/bf_onboarding.xml",
        "views/res_config_settings_views.xml",
        "views/webclient_brand_icon.xml",
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
            # Opens "Install App" to every app, not Odoo's three. See the file.
            "bluefox_branding/static/src/js/install_app_menu.js",
            # Second worker registration, scoped to /scoped_app.
            "bluefox_branding/static/src/js/scoped_app_worker.js",
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

