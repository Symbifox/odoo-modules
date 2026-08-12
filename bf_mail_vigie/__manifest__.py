{
    "name": "BF Vigie courriels (re-router)",
    "version": "18.0.2.2.0",
    "category": "Productivity/Email",
    "summary": "Bouton 'Re-router' sur bf.email pour d\u00e9placer un courriel mal rout\u00e9",
    "description": """
Ajoute un bouton "Re-router" sur la liste et le formulaire de `bf.email`.
Analyse les headers In-Reply-To / References du courriel pour sugg\u00e9rer
la bonne chatter cible. L'utilisateur confirme, on d\u00e9place le
mail.message (mise \u00e0 jour des colonnes model/res_id), sans renvoi ni
notification.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    'license': 'LGPL-3',
    "depends": ["mail", "bf_email_management", "bf_onboarding_base"],
    "data": [
        "security/ir.model.access.csv",
        "wizard/reroute_wizard_views.xml",
        "views/bf_email_views.xml",
        "data/bf_onboarding.xml",
    ],
    "application": False,
    "installable": True,
}
