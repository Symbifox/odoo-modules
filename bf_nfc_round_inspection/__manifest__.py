{
    "name": "Pastilles NFC : tournées avec relevé",
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "summary": "Un point de tournée qui porte une grille : le passage et le relevé d'un seul tapotement",
    "description": """
Pastilles NFC : tournées avec relevé
====================================

Pont entre les tournées et les relevés, installé de lui-même quand les deux sont
là. Un point de tournée peut porter une grille : le tapotement affiche la grille,
et l'enregistrer note le passage ET écrit le relevé. La ronde incendie du mois
devient un registre, point par point, sans deuxième pastille.

Le guet des relevés manqués couvre aussi ces points : un extincteur dont la grille
est mensuelle et que la ronde n'a pas relevé ce mois-ci crée une activité.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc_round", "bf_nfc_inspection"],
    "data": ["views/bf_nfc_round_views.xml", "data/bf_nfc_round_inspection_gabarits.xml"],
    "installable": True,
    "application": False,
    "auto_install": True,
}
