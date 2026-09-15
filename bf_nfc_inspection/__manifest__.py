{
    "name": "Pastilles NFC : relevés et registres",
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "summary": "Une grille remplie à chaque tapotement, les anomalies suivies jusqu'à leur correction, et le registre qui en sort",
    "description": """
Pastilles NFC : relevés et registres
====================================

Un passage horodaté prouve qu'on est venu. Il ne prouve pas ce qu'on a vu. Un
registre d'inspection, lui, demande un résultat par élément : l'extincteur est-il
accessible, sa pression dans la zone verte, ses sceaux intacts ? Et quand un
élément ne va pas, ce qui a été fait, et quand.

* **Une grille** : les éléments à vérifier, chacun conforme / non conforme, une
  valeur mesurée avec sa plage, un choix ou un texte.
* **Un relevé par tapotement** : le téléphone affiche la grille, on la remplit sur
  place, avec ou sans réseau.
* **Les anomalies** : une activité pour la personne responsable, et une correction
  datée à consigner sur le relevé.
* **Le registre** : un PDF qui reprend les colonnes d'un registre papier (date,
  élément, personne, résultat, correction et sa date).
* **Le guet** : une pastille dont la grille est mensuelle et qui n'a pas été relevée
  ce mois-ci crée une activité, une seule fois par période.

Les grilles livrées sont des points de départ, à adapter à l'équipement réel et à
valider contre la norme qui s'applique.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_nfc_inspection_rules.xml",
        "data/bf_nfc_inspection_data.xml",
        "data/bf_nfc_inspection_grilles.xml",
        "views/bf_nfc_checklist_views.xml",
        "views/bf_nfc_reading_views.xml",
        "views/bf_nfc_template_views.xml",
        "data/bf_nfc_inspection_gabarits.xml",
        "report/bf_nfc_registre.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
