{
    "name": "Reprise après migration Enterprise",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Récupérer les données qu'une migration Enterprise vers Community "
               "a laissées de côté, et contrôler ce qui reste à régler",
    "description": """
Reprise après migration Enterprise
==================================

Une migration d'Odoo Enterprise vers Community ne perd pas les données par
accident : elle les perd par construction. Une base Community n'a pas de table
``helpdesk_ticket``, pas de ``knowledge_article``, pas de ``sign_template``.
L'outil d'import les écarte parce qu'il n'a nulle part où les écrire, en fait
le compte dans son sommaire, et c'est fini — les rangs disparaissent avec le
dump.

``odoo18-ee2ce`` écrit désormais ces rangs dans un fichier plutôt que de les
laisser tomber. Ce module est l'autre bout : on dépose le fichier, il montre ce
qu'il contient, et il reloge ce qui a une destination crédible.

Deux choses, dans cet ordre
---------------------------

**Garder.** Chaque table reprise devient un jeu d'enregistrements consultable,
cherchable et exportable, qu'une correspondance existe ou non. C'est le socle :
avant de savoir où mettre un billet d'assistance, il faut ne pas l'avoir perdu.

**Reloger.** Quatre correspondances sont livrées, chacune avec ce qu'elle ne
sait pas transporter, affiché avant d'écrire quoi que ce soit :

* les billets d'assistance vers ``helpdesk_mgmt`` (OCA) ;
* les articles Knowledge vers ``document_page`` (OCA) ;
* les modèles de signature vers ``bf_sign`` ;
* les plans d'abonnement vers ``contract`` (OCA).

Un aperçu montre les dix premiers rangs tels qu'ils seraient écrits, avec la
liste de ce qui a été écarté et pourquoi. Chaque rang est créé dans son propre
point de reprise : un rang qui tombe ne coûte pas les autres.

Les contrôles d'après-migration
-------------------------------

L'outil d'import imprime une liste de choses à finir, puis rend la main — et la
liste vit le temps de la fenêtre de terminal. Les mêmes contrôles tournent ici
contre l'instance vivante : langues employées mais inactives, ``web.base.url``
resté sur la valeur de neutralisation, actions planifiées encore arrêtées,
séquences en retard sur leur table, pièces jointes dont le fichier n'a pas
suivi, serveurs d'envoi rallumés trop tôt.

Un contrôle distingue trois réponses. **Rien à faire** veut dire qu'il a
regardé. **À corriger** veut dire qu'il a trouvé. **Non vérifiable** veut dire
qu'il n'a pas pu regarder — et ça se voit, au lieu de compter pour un succès.

Ce que ce module ne fait pas
----------------------------

* **Migrer.** Il ne touche ni au dump, ni à Docker, ni au schéma. Il lit un
  fichier déposé sur un formulaire, comme n'importe quelle pièce jointe.
* **Deviner un schéma Enterprise.** Les correspondances s'appliquent aux
  colonnes réellement présentes dans le fichier ; une colonne inconnue reste
  sur le rang plutôt que d'être inventée ou jetée.
* **Faire confiance aux identifiants.** Chaque référence est vérifiée dans le
  modèle d'arrivée et écartée, avec sa raison, quand la fiche n'existe pas.
* **Rendre les pavés de signature ni la récurrence d'un abonnement.** Ces
  formes-là ne se transposent pas ; le module le dit sur la table concernée
  plutôt que de produire des fiches qui ont l'air complètes.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
    ],
    "data": [
        "security/bf_oe2oc_security.xml",
        "security/ir.model.access.csv",
        "views/bf_oe2oc_views.xml",
        "views/bf_oe2oc_check_views.xml",
        "views/bf_oe2oc_menus.xml",
        "data/bf_oe2oc_check_data.xml",
    ],
    "installable": True,
    "application": True,
}
