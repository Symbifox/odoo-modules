{
    "name": "Fédération : livrables remis",
    "version": "18.0.1.1.0",
    "category": "Project",
    "summary": "Remettre un document à un pair fédéré, daté et versionné, et savoir qu'il l'a lu",
    "description": """
Fédération : livrables remis
============================

Un compte rendu, une cartographie, une politique, une procédure, un échéancier,
un relevé : ce sont le même objet. Un titre, une version, une date, un fichier,
et une seule chose qui revient, **l'accusé de réception**.

* **Un genre pour tous** : plutôt qu'un contrat par modèle, la remise porte ce
  que le PDF montre déjà. Elle n'est pas une divulgation nouvelle, c'est le même
  contenu sous une autre forme.
* **Une version remplace la précédente** chez le pair, sans effacer l'historique :
  chaque remise est un enregistrement daté de plus.
* **L'accusé revient** avec le nom de qui a lu et la date. Rien d'autre ne revient.
* **Le fichier voyage sous le plafond du pair** ; au-dessus, la remise arrive avec
  son titre, sa version et un lien, et le fichier reste chez l'émetteur.
* Quand `project_knowledge_matrix` est là, l'accusé se reporte sur la
  distribution du document d'origine, qui le modélisait déjà.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_federation"],
    "data": [
        "security/ir.model.access.csv",
        "views/federation_document_views.xml",
        "views/federation_document_menu.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
