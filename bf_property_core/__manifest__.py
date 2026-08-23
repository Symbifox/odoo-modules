{
    "name": "Copropriété — Socle",
    "summary": "Syndicats de copropriété : immeubles, fractions, quotes-parts et copropriétaires",
    "version": "18.0.1.1.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — « Other proprietary » est la seule valeur du vocabulaire
    # Odoo qui puisse la porter. Le fichier LICENSE fait foi. P0.3 a été fermée
    # le 2026-08-22 : l'Additional Use Grant nomme le cas du gestionnaire qui
    # administre pour le compte d'autrui, celui-là même que P0.4 a écarté du
    # segment, et qui tombait sur la couture de « internal business operations ».
    # ⚠️ La Change Date se RETAMPONNE À LA PUBLICATION, date de publication de
    # la version + 4 ans : le plafond de la BUSL se compte par version, et c'est
    # la plus proche des deux dates qui gagne. Procédure en P6.2.
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "auto_install": False,
    "description": """
Copropriété — Socle
===================

Socle de gestion pour les syndicats de copropriété divise du Québec.

**Structure**
  - Syndicat : la personne morale, sa déclaration de copropriété et sa base
    de quotes-parts (millièmes par défaut)
  - Immeuble : adresse, lot cadastral, année de construction
  - Fraction : partie privative, sa quote-part, sa superficie et son type
    (résidentiel, stationnement, rangement, commercial)
  - Partie commune : générale ou à usage restreint, avec les fractions qui
    en ont la jouissance

**Copropriétaires**
  - Historique de propriété par fraction, avec dates d'entrée et de sortie
  - Indivision : plusieurs copropriétaires sur une même fraction, avec leur
    part respective
  - Occupant distinct du propriétaire lorsque la fraction est louée

**Contrôle des quotes-parts**
  Le total des quotes-parts est calculé en continu et comparé à la base
  déclarée du syndicat. L'écart est signalé sans bloquer la saisie, parce
  qu'un immeuble se saisit fraction par fraction.

Ce module ne fait que la structure. La gouvernance (assemblées, votes),
les charges communes et le fonds de prévoyance vivent dans des modules
distincts.
""",
    "depends": [
        "base",
        "mail",
    ],
    "data": [
        "security/bf_property_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/bf_property_syndicat_views.xml",
        "views/bf_property_building_views.xml",
        "views/bf_property_unit_views.xml",
        "views/bf_property_ownership_views.xml",
        "views/bf_property_common_area_views.xml",
        "views/bf_property_menus.xml",
    ],
}
