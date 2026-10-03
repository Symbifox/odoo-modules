{
    "name": "BF Bureau — vues multi-panneaux",
    "summary": "Crée et gère des « bureaux » nommés (mises en page multi-panneaux d'actions Odoo) modifiables depuis l'UI",
    # 18.0.3.3.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.3.3.1: essais d'isolation entre personnes seulement ; aucun code
    #   changé.
    # 18.0.3.3.2: une personne non administratrice crée ses panneaux. La
    #   contrainte de mode de vue lisait ir.actions.act_window sous ses droits
    #   (fermé aux internes) et levait une AccessError. Le sélecteur d'action
    #   du formulaire, même cause : il cherche en superutilisateur et ne rend
    #   que les actions qu'on pourrait ouvrir (groupe, modèle lu), « Recherche
    #   avancée » comprise (vues des actions lisibles par tout interne : ce ne
    #   sont pas des données).
    "version": "18.0.3.3.2",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    'license': 'LGPL-3',
    "application": True,
    "installable": True,
    # ⚠️ `bf_email_management` reste volontairement ABSENT : ce module est
    # LGPL-3, celui-là est BUSL-1.1, et une dépendance dure ferait promettre à
    # la licence permissive ce qu'elle ne peut pas tenir. La tuile courriel du
    # bureau par défaut est posée à l'installation par `hooks.py`.
    "depends": ["web", "base", "mail", "project", "bf_onboarding_base"],
    "post_init_hook": "post_init_hook",
    "data": [
        "security/ir.model.access.csv",
        "security/bf_bureau_security.xml",
        "views/bf_bureau_views.xml",
        "views/bf_bureau_menu.xml",
        "data/bf_bureau_default_desk.xml",
        "data/bf_onboarding.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_bureau/static/src/js/bf_bureau_desk.js",
            "bf_bureau/static/src/xml/bf_bureau_desk.xml",
            "bf_bureau/static/src/scss/bf_bureau_desk.scss",
        ],
    },
}
