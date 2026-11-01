{
    "name": "Symbifox — Courriels multi-sociétés",
    "summary": "Signatures courriel normalisées et pilotées par gabarit + "
               "adresse d'expédition et routage catchall/bounce par société",
    "version": "18.0.5.0.3",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    # Clean, self-contained replacement for the third-party
    # ``ous_multi_company_email`` module. It reproduces the three features that
    # module users relied on — per-company signature, per-company sender address,
    # per-company catchall/bounce/default-from override — WITHOUT the two
    # destructive behaviours of that module: it never rewrites the partner's
    # email, and it never mutates the user's default company on a company
    # switch. Signatures are driven by the user's company_id (employee profile).
    "depends": [
        "base",
        "mail",
        "hr",
        # Le menu « Ma signature courriel » vit sous Courriels > Configuration.
        # Les signatures sont de la configuration, pas un espace de travail :
        # pas de tuile d'application, pas de menu racine à elles. Le prix de ce
        # choix est cette dépendance, assumée.
        "bf_email_management",
        # Les liens rapides publient une adresse /r/<code> : changer la cible
        # redirige toutes les signatures déjà collées, sans que personne
        # recolle la sienne. C'est le module d'Odoo qui sert cette route.
        "link_tracker",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/signature_security.xml",
        "security/server_action.xml",
        "data/bf_signature_templates.xml",
        "data/bf_signature_gallery.xml",
        "data/signature_link_cron.xml",
        "data/bf_company_data.xml",
        "wizard/bf_signature_gallery_views.xml",
        "views/bf_signature_link_views.xml",
        "views/mail_template_views.xml",
        "views/res_company_views.xml",
        "views/res_users_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "post_init_hook": "post_init_hook",
}
