{
    "name": "Relations de travail : côté syndical",
    "summary": "Adhésions, cotisations perçues et rapprochement, assemblées et "
               "votes, délégués et libérations, le grief vu du plaignant",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Relations de travail : côté syndical
====================================

Le pendant du greffon employeur. Le socle décrit ce que la relation **est** ;
celui-ci porte ce que le syndicat **fait**.

Ce qu'il ajoute
---------------

* **Les adhésions** (`bf.labour.card`), avec signature, retrait et réadhésion.
  Une personne quitte et revient ; l'historique reste, parce que c'est lui qui
  répond à « depuis quand est-elle membre ».
* **Le rapprochement des cotisations** (`bf.labour.dues.receipt`) : ce que le
  syndicat a reçu, contre ce que l'employeur a déclaré remettre. **L'écart est
  le chiffre intéressant**, et c'est la seule raison d'exister du modèle.
* **Les assemblées et les votes** (`bf.labour.assembly`, `bf.labour.assembly.vote`).
  🔴 Le droit de vote suit l'ADHÉSION, jamais la couverture : c'est le miroir
  exact de la cotisation, qui suit la couverture et jamais l'adhésion. Les deux
  états du socle servent ici à des choses opposées, et c'est pour ça qu'ils
  sont deux.
* **Les délégués et leurs libérations** (`bf.labour.delegate`), avec les heures
  prévues par la convention et les heures prises.
* **Le grief vu du plaignant** : mandat, personne-ressource, décision de porter
  à l'arbitrage. Des champs sur le grief du socle, jamais un second grief.

Ce qu'il ne fait pas
--------------------

* Il ne duplique pas le grief. Les deux côtés lisent le même dossier, sinon ils
  cessent de parler de la même chose.
* Il ne calcule pas les cotisations. Il enregistre ce qui a été reçu et le
  compare à ce que l'employeur déclare.
""",
    "depends": [
        "bf_labour_relations",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/union_security.xml",
        "views/card_views.xml",
        "views/assembly_views.xml",
        "views/delegate_views.xml",
        "views/dues_receipt_views.xml",
        "views/grievance_views.xml",
        "views/menuitems.xml",
    ],
}
