{
    "name": "Persona des contacts - expérience client",
    "summary": "Le persona montre les notes, commentaires et plaintes d'expérience client du contact",
    # 18.0.1.1.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.1.1.1: les libellés de sélection de bf_cx (source anglaise depuis
    #   18.0.1.13.2) se lisent dans la langue du résumé, même quand l'appel
    #   n'en porte aucune (contexte de Gen, crochet de création, travail
    #   planifié) : on lisait « NPS 6/10 (detractor) » dans une phrase française.
    "version": "18.0.1.1.1",
    "category": "Sales/CRM",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Persona des contacts ↔ Expérience client
=============================================

S'auto-installe quand bf_persona et bf_cx sont tous deux installés.

Le persona d'un contact montre sa dernière note (NPS ou satisfaction) avec son
commentaire, ses plaintes ouvertes et son exclusion des sollicitations. Le
bandeau du composeur et le contexte donné à Gen le reprennent : une plainte
ouverte rend la relation « dégradée », un détracteur ou une note basse la met
« à surveiller », avec la raison en toutes lettres.
""",
    "depends": [
        "bf_persona",
        "bf_cx",
    ],
    "data": [
        "views/contact_persona_views.xml",
    ],
}
