{
    "name": "Organigrammes : le moteur de dessin",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Une géométrie, deux rendus : l'organigramme à l'écran et le même en PDF",
    "description": """
Organigrammes : le moteur de dessin
===================================

Le socle ne dessine l'organigramme de personne. Il fournit le dessin, et les
satellites fournissent les données.

* **Une carte** (des boîtes, des arêtes) rendue par un modèle qui signe le
  contrat ``bf.org.chart.source``.
* **Une géométrie**, calculée une seule fois, repli des libellés compris.
* **Deux rendus** qui tracent exactement le même plan : le SVG de l'écran et
  le PDF vectoriel, en Lexend, aux couleurs de la maison.

Deux dispositions, choisies par la forme des données :

* **arbre** quand chaque boîte a au plus un parent, parent centré sur ses
  enfants;
* **couches** dès qu'une boîte en a plusieurs. Une détention partagée n'est
  pas un arbre : elle se range en niveaux, les arêtes qui sautent un niveau
  obtiennent un couloir réservé, et l'information qu'elles portent (un
  pourcentage) reste lisible.

Une boucle de saisie ne fait pas tomber le dessin : l'arête qui la referme est
écartée et la page le dit.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["base", "web"],
    "data": [
        "views/templates.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
