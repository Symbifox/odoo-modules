{
    "name": "Registre de formation : contenus SCORM",
    "version": "18.0.1.0.1",
    "category": "Human Resources",
    "summary": "Jouer un paquet SCORM 1.2 ou 2004 dans l'eLearning d'Odoo, et porter "
               "sa complétion au registre",
    "description": """
Registre de formation : contenus SCORM
======================================

Odoo 18 ne connaît **aucun** format de cours normalisé : son lecteur accepte une
image, un article, un document, une vidéo ou un questionnaire, et c'est tout.
Un organisme qui livre sa formation en SCORM — la quasi-totalité d'entre eux —
n'a donc aucun moyen de la faire jouer, ni de faire remonter le résultat.

Ce module ajoute le type manquant.

* **Un paquet SCORM devient un contenu du cours.** On dépose le ``.zip``, le
  module lit son ``imsmanifest.xml``, en tire la version et le point d'entrée.
* **Le contenu parle au module** par l'API que la norme impose :
  ``window.API`` pour SCORM 1.2, ``window.API_1484_11`` pour SCORM 2004. Les
  deux, parce qu'un catalogue de formation en contient toujours des deux.
* **La complétion remonte jusqu'au registre.** Le module marque le contenu
  terminé par le crochet du lecteur, et le pont eLearning écrit la réalisation
  datée. Rien n'est réinventé.

Ce que le module NE fait pas, et le dit
----------------------------------------

**Le séquencement et la navigation de SCORM 2004 ne sont pas implémentés.** La
norme SN est un automate à états de plusieurs centaines de règles, que presque
aucun contenu n'exerce vraiment. Un paquet qui en dépend jouera son premier SCO
et s'arrêtera là. Le module le signale sur la fiche plutôt que de faire semblant :
un lecteur qui prétend séquencer et se trompe est pire qu'un lecteur qui annonce
qu'il ne séquence pas.

Ce qu'il refuse
---------------

* **Un paquet dont le manifeste ne se lit pas est refusé au dépôt**, pas accepté
  et cassé au premier clic.
* **Une tentative sans état terminal ne vaut pas un échec.** ``incomplete`` et
  ``not attempted`` restent ce qu'ils sont ; seuls ``completed``, ``passed`` et
  leurs équivalents 2004 marquent le contenu terminé.
* **Le module ne sert que des fichiers du paquet.** Un chemin qui sort du paquet
  est refusé, quelle que soit sa forme.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_training_slides"],
    "data": [
        "security/ir.model.access.csv",
        "security/scorm_security.xml",
        "views/scorm_views.xml",
        "views/slide_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "bf_training_scorm/static/src/js/scorm_api.js",
        ],
    },
    "installable": True,
    "application": False,
}
