{
    "name": "Registre de formation : accusé signé par module",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "summary": "Lire une politique et le confirmer par écrit devient une ligne du "
               "registre, datée et rattachée à la version lue",
    "description": """
Registre de formation : accusé signé par module
===============================================

Certaines formations ne sont pas des cours : ce sont des **documents à lire**, et
la preuve attendue est un accusé daté, parfois signé, versé au dossier de la
personne. Une politique de prévention, un processus d'accueil en plusieurs
modules, une procédure qui change.

La maison sait déjà distribuer un document versionné et recueillir un accusé. Ce
pont fait que cet accusé **compte comme une formation suivie**.

* **Une activité s'adosse à un document.** Distribuer une version à quelqu'un,
  c'est lui assigner la formation.
* **L'accusé écrit la réalisation**, datée du moment où il arrive, et rattachée à
  **la version lue**, pas au document en général.
* **Une signature exigée est exigée pour de vrai.** Tant qu'elle manque, l'accusé
  ne vaut pas preuve et aucune ligne n'est écrite.
* **Une nouvelle version rouvre l'obligation** de ceux qui n'ont accusé que
  l'ancienne. C'est le cas d'usage : une politique qui change doit être relue.

Un module par module
--------------------

Quand un processus d'accueil comporte plusieurs modules, chacun a son document,
donc sa propre activité, sa propre distribution et son propre accusé. C'est la
forme que la réglementation demande, et c'est aussi la seule qui permette de dire
lequel manque.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_training", "project_knowledge_matrix"],
    "data": [
        "views/training_activity_views.xml",
        "views/training_record_views.xml",
        "views/document_distribution_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
