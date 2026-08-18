{
    "name": "BF Cible de chatter",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Sélecteur unique de fiche cible pour tous les importateurs de chatter",
    "description": """
Socle partagé par les importateurs Blue Fox
===========================================

Avant ce module, chaque importateur portait sa propre façon de désigner la fiche
qui reçoit le contenu : un ``Reference`` sur tous les modèles porteurs de chatter
ici, une liste de douze modèles codée en dur là, un couple « Projet + Tâche »
ailleurs. Deux résolveurs de « lien rapide » avaient forké.

Ce module en fait une seule brique.

- ``bf.chatter.target`` : la liste des modèles compatibles, le résolveur de
  référence (URL Odoo, numéro, ``task:22299``, ``bf.email:17``,
  ``INV/2026/00017``) et la recherche transversale ``search_targets``.
- ``bf.chatter.target.mixin`` : le champ ``target_reference`` et ses gardes
  d'accès, à hériter dans un assistant.
- le widget ``bf_chatter_target`` : une seule zone de saisie, résultats groupés
  par modèle avec icône et ligne de contexte, comme la palette de la recherche
  universelle. Plus besoin de choisir le type d'objet ni le projet.

``bf_universal_search`` est utilisé s'il est installé (ses configurations donnent
les icônes, les lignes de contexte et le biffage des fiches terminées) ; sinon la
recherche retombe sur ``name_search``.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["web", "mail"],
    # Aucune donnée : les deux modèles sont abstraits, donc ni table ni ACL.
    "data": [],
    "assets": {
        "web.assets_backend": [
            "bf_chatter_target/static/src/scss/chatter_target_picker.scss",
            "bf_chatter_target/static/src/js/chatter_target_picker.js",
            "bf_chatter_target/static/src/xml/chatter_target_picker.xml",
        ],
    },
    "installable": True,
    "application": False,
}
