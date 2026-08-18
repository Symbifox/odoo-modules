# -*- coding: utf-8 -*-
{
    "name": "Documents — Approbation à plusieurs",
    "version": "18.0.1.0.0",
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
        "views/document_approval_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
