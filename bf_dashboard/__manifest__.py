{
    "name": "Tableau de bord Symbifox",
    "summary": "Tableau de bord unifi\u00e9 agr\u00e9geant facturation, h\u00e9bergement, connaissances et vie priv\u00e9e",
    "version": "18.0.1.2.0",
    "category": "Services",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # Volontairement court. hosting_management, project_knowledge_matrix et
    # privacy_consent ont été retirés d'ici (tâche #24862) : ils sont sondés à
    # l'exécution par @needs, donc leur tuile apparaît quand le locataire les
    # porte et disparaît sinon, au lieu de les lui imposer tous les trois.
    # account, project et mail restent en dur — les collecteurs comptables
    # interrogent account_move_line en SQL brut, hors de portée de toute garde.
    "depends": [
        "base",
        "account",
        "project",
        "mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/dashboard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_dashboard/static/src/js/bf_dashboard.js",
            "bf_dashboard/static/src/xml/bf_dashboard.xml",
        ],
    },
}
