{
    "name": "Pastilles NFC : les tournées",
    "version": "18.0.1.1.0",
    "category": "Productivity",
    "summary": "Une suite de pastilles à taper dans l'ordre, et une alerte quand un point est manqué",
    "description": """
Pastilles NFC : les tournées
============================

Une ronde de sécurité, une tournée d'entretien, une inspection d'immeuble : une
pastille à chaque point de passage. On les tape en passant ; le téléphone dit
« point 3 sur 7, prochain : salle des serveurs ».

* **La preuve de passage** : qui, à quel point, à quelle heure. Un point tapé
  sans réseau (sous-sol, local technique) garde l'heure du téléphone.
* **L'ordre** : une tournée « dans l'ordre » marque le point tapé trop tôt.
* **L'alerte** : une tournée commencée et pas finie dans sa durée, ou une tournée
  à horaire que personne n'a faite dans sa fenêtre, crée une activité pour la
  personne responsable. Une seule alerte par manquement.

Les points se composent dans la fiche de la tournée ; chaque point reçoit une
pastille « Point de tournée », gravée depuis « Mes pastilles » dans l'application.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc", "mail"],
    "external_dependencies": {"python": ["pytz"]},
    "data": [
        "security/ir.model.access.csv",
        "security/bf_nfc_round_rules.xml",
        "data/bf_nfc_round_data.xml",
        "views/bf_nfc_round_views.xml",
        "views/bf_nfc_template_views.xml",
        "data/bf_nfc_round_gabarits.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
