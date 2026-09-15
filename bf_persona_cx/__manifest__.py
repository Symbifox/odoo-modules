{
    "name": "Persona des contacts - expérience client",
    "summary": "Le persona montre les notes, commentaires et plaintes d'expérience client du contact",
    "version": "18.0.1.0.0",
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
