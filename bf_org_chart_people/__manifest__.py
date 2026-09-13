{
    "name": "Organigramme des personnes",
    "version": "18.0.1.1.0",
    "category": "Productivity",
    "summary": "Qui relève de qui, sur les contacts, avec le dessin et son PDF",
    "description": """
Organigramme des personnes
==========================

Un champ **Supérieur immédiat** sur la fiche de contact, et la chaîne de
commande se dessine.

* La **vue hiérarchique native** d'Odoo 18 donne l'écran interactif, sans une
  ligne de JavaScript : on déplie, on replie, on déplace une carte pour
  changer son supérieur.
* Le **dessin du socle** donne la même chaîne en page imprimable et en PDF,
  pour ce qui sort de l'écran et part chez le client.

⛔ Le champ « Société » n'est pas détourné. Il dit où la personne travaille;
il ne dit pas de qui elle relève, et s'en servir pour ça réécrit l'adresse de
la fiche avec celle du parent, en silence.

Un supérieur peut travailler dans une autre entreprise du groupe : c'est le cas
courant chez un holding qui fournit des services partagés. La saisie le
signale, elle ne le refuse pas.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    # `web_hierarchy` porte le TYPE de vue `hierarchy`. Il est déjà installé
    # partout chez nous parce que `hr_org_chart` l'entraîne avec Employés,
    # mais s'appuyer sur cet accident ferait tomber le module sur une base qui
    # n'a pas le module RH.
    "depends": ["bf_org_chart", "contacts", "web_hierarchy"],
    "data": [
        "views/res_partner_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
