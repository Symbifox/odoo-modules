# -*- coding: utf-8 -*-
{
    "name": "Documents — Approbation à plusieurs",
    "version": "18.0.1.1.0",
    "category": "Services/Project",
    "summary": "Une politique n'est publiée que lorsque tous ceux qui devaient"
               " se prononcer l'ont fait, et sa diffusion suit le RACI",
    "description": """
Documents — Approbation à plusieurs
===================================

Compagnon de ``project_knowledge_matrix``. Une version de document y porte
déjà un approbateur, une date et un cycle brouillon → révision → approuvé →
publié. Un seul approbateur, et ``action_release`` approuve d'office si
personne ne l'a fait : une politique pouvait donc être publiée d'un clic, sans
que quiconque se soit prononcé.

Ce module ajoute ce qui manquait :

* **des approbateurs nommés** sur une version, chacun avec son avis, sa date et
  son commentaire ;
* **un verrou** : tant qu'un avis requis est en attente, la version ne
  s'approuve ni ne se publie. Un refus bloque et se dit ;
* **la diffusion qui suit le RACI** : à la publication, les parties prenantes
  *informées* des éléments de matrice du document reçoivent leur distribution,
  avec l'accusé de réception et, au besoin, la signature que
  ``project.document.distribution`` gère déjà.

Qui peut quoi (1.1.0)
---------------------
* **L'avis appartient à la personne nommée.** Elle seule approuve ou refuse,
  et elle seule écrit son motif. ``avis`` et ``date_avis`` ne s'écrivent
  jamais directement, pas même par un gestionnaire : seuls les boutons les
  posent, les datent et le disent au fil, au nom de qui a cliqué.
* **Le tour de table se compose** (ajouter, retirer, rendre facultatif) par le
  responsable du document, à défaut son auteur, et par les gestionnaires des
  documents.
* **Un avis donné ne s'efface pas.** Sa ligne ne se retire plus et ne change
  plus de titulaire ni de caractère requis : on ne retire pas celui qui a
  refusé pour publier. Pour reprendre le tour de table, une nouvelle version.
* Ce qui affaiblit le verrou sur une ligne encore en attente (retrait,
  remplacement, avis rendu facultatif) s'écrit au fil du document.
* Les lignes suivent la visibilité des versions : société, membres du projet,
  gestionnaires. La personne nommée voit toujours la sienne.

Avant 1.1.0, tout utilisateur interne pouvait approuver à la place d'un
autre (le fil disait alors que l'autre avait approuvé), écrire l'avis par RPC
ou retirer un refus, puis publier.

Ce qu'il ne fait pas
--------------------
Pas de paliers conditionnels ni de délégation : ``base_tier_validation`` de
l'OCA fait cela très bien. Il est sous licence AGPL-3, incompatible avec la
redistribution de cette suite, et le besoin exprimé — « ils ont voté, ça a été
approuvé » — n'appelle pas un moteur de paliers.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["project_knowledge_matrix"],
    "data": [
        "security/ir.model.access.csv",
        "security/approbation_security.xml",
        "views/document_approval_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
