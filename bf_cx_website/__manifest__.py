{
    "name": "Expérience client : témoignages sur le site web",
    "summary": "Page publique /temoignages rendue dynamiquement depuis les témoignages publiés",
    "version": "18.0.1.1.0",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Site web
=================================

S'auto-installe quand bf_cx et website sont tous deux installés.
Publie la page publique /temoignages, qui rend dynamiquement les
témoignages en état « Publié » : citation, nom du client et société
du client, filtrés sur la société du site web courant. Aucun menu de
site n'est ajouté : le propriétaire du site choisit où lier la page.
Un témoignage retiré disparaît instantanément du site puisque le
rendu est dynamique (retrait de consentement, Loi 25).
""",
    "depends": [
        "bf_cx",
        "website",
    ],
    "data": [
        "views/website_templates.xml",
    ],
}
