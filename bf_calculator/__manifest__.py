{
    "name": "BF Calculatrice",
    "version": "18.0.1.2.0",
    "category": "Productivity",
    "summary": "Calculatrice de barre du haut : historique, descriptifs, taxes, heures, versement au chatter",
    "description": """
Calculatrice Symbifox
=====================

Une icône dans la barre du haut ouvre une calculatrice qui garde l'historique
de chaque personne et verse un calcul, descriptifs compris, en note interne au
chatter de la fiche ouverte.

- saisie à la française (virgule décimale, espaces de milliers) ;
- descriptifs dans l'opération : ``6 (semaines) * 750 (dollars) =`` ;
- modes Taxes (taux lus en comptabilité, taxe inversée), Heures,
  Pourcentages et marges, Colonne collée ;
- insère un résultat dans le dernier champ numérique cliqué d'un formulaire ;
- valeur résiduelle comme une vraie calculatrice, variables (``taux = 125``),
  mémoire M, épingles, rappel par la flèche haut, arrondi au 5 ¢ ;
- conversion de devises aux taux de la Banque du Canada (``100 USD en CAD``) ;
- dates : jours entre deux dates, date + N jours ouvrables (fériés CNESST).
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["web", "mail", "bf_chatter_target"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_calculator_security.xml",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/bf_calculator_entry_views.xml",
        "views/bf_calculator_post_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_calculator/static/src/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
