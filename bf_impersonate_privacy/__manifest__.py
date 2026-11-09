{
    "name": "Incarnation — pont Vie privée (Loi 25)",
    "summary": "Finalité, calendrier de conservation et purge du journal des "
               "incarnations, avec inscription au registre de destruction",
    "version": "18.0.1.0.0",
    "category": "Administration",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # ⚠️ PAS d'auto_install : une durée de conservation est une décision
    # d'entreprise, prise par le responsable de la vie privée de CETTE
    # entreprise-là et inscrite à SON registre. bf_impersonate est publié et
    # posé sur cinq locataires ; ce pont s'installe à la main, locataire par
    # locataire. Même raisonnement que bf_sms_privacy et bf_email_privacy.
    #
    # Propriétaire sous BUSL-1.1 (date de bascule au LICENSE), et non LGPL
    # comme bf_impersonate lui-même.
    "depends": [
        "bf_impersonate",
        "privacy_consent",
    ],
    "description": """
Incarnation — pont Vie privée (Loi 25)
======================================

Le journal des incarnations (`bf_impersonate`) dit qui a vu Symbifox en tant
que qui, pourquoi, pendant combien de temps, ce qui a été changé, ce qui a été
consulté, et depuis quelle adresse IP. Ce sont des renseignements personnels
sur deux personnes à la fois : celle qui est vue et celle qui regarde.

Sur une instance en service, aucune finalité du registre ne visait
l'incarnation, aucun calendrier de conservation ne s'appliquait au journal, et
rien ne le purgeait : il grossissait sans borne.

Ce que ce pont fait
-------------------

* Une finalité au registre : surveiller et pouvoir rendre compte d'un accès
  administratif au compte d'une personne.
* Deux calendriers de conservation, parce que les deux moitiés du journal
  n'ont pas la même valeur de preuve :

  * INC-001, le détail (ce qui a été changé, ce qui a été consulté, les noms
    des fiches touchées, l'adresse IP) : **1 an**, aligné sur AGD-001, le
    journal des accès aux liens de partage d'agenda ;
  * INC-002, l'en-tête (qui, en tant que qui, pourquoi, quand, combien de
    temps, comment la séance a fini) : **3 ans**, aligné sur COR-001. Sans
    lui, plus rien ne prouverait qu'une séance a eu lieu.

* Une purge quotidienne qui exécute les deux durées et inscrit chaque lot au
  registre de destruction. Elle efface pour vrai, comme le ménage de
  `bf_editorial_audience_privacy`, et pour la même raison : un journal d'accès
  que personne ne purge est le manquement, pas la purge.

Ce qu'il ne fait pas
--------------------

* Il ne touche pas aux gardes de `bf_impersonate` ni à ses droits : personne
  n'écrit au journal, et la purge passe par le code du pont en `sudo()`.
* Il ne demande aucun consentement. Un accès administratif encadré, journalisé
  et notifié relève de la sécurité du système et de l'obligation de rendre
  compte, pas d'une faveur qu'on accorde. Un consentement qu'on ne peut pas
  refuser n'est pas un consentement.
""",
    "data": [
        "data/privacy_purpose_data.xml",
        "data/privacy_retention_calendar_data.xml",
        "data/bf_impersonate_privacy_cron.xml",
    ],
}
