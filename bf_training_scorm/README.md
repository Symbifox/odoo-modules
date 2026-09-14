# Registre de formation : contenus SCORM

Le lecteur SCORM manquant d'Odoo 18, pour le registre de formation, développé par
[Les services de consultation Blue Fox, Inc.](https://symbifox.com)

Odoo 18 ne connaît **aucun** format de cours normalisé. Son lecteur accepte cinq
types de contenu — image, article, document, vidéo, questionnaire — et c'est tout.
Un organisme qui livre sa formation en SCORM, ce qui est le cas de la quasi-totalité
d'entre eux, n'a donc aucun moyen de la faire jouer dans Odoo, ni de faire remonter
le résultat.

Ce module ajoute le type manquant.

## Ce qu'il fait

- **Un paquet SCORM devient un contenu du cours.** On dépose le `.zip`, le module
  lit son `imsmanifest.xml`, en tire la version et le point d'entrée.
- **Le contenu parle au module** par l'API que la norme impose : `window.API` pour
  SCORM 1.2, `window.API_1484_11` pour SCORM 2004. Les deux, parce qu'un catalogue
  de formation en contient toujours des deux.
- **La complétion remonte jusqu'au registre.** Le module marque le contenu terminé
  par le crochet du lecteur natif, et le pont eLearning écrit la réalisation datée.
  Rien n'est réinventé, et il n'y a qu'un seul chemin d'écriture au registre.

## Ce que le module NE fait pas, et le dit

**Le séquencement et la navigation de SCORM 2004 ne sont pas implémentés.** La
norme SN est un automate à états de plusieurs centaines de règles, que presque aucun
contenu n'exerce vraiment. Un paquet qui en dépend jouera son premier SCO et
s'arrêtera là.

Le module l'annonce sur la fiche du paquet et sur la page de lecture, plutôt que de
faire semblant. **Un lecteur qui prétend séquencer et se trompe est pire qu'un
lecteur qui annonce qu'il ne séquence pas** : le premier laisse croire qu'un
parcours a été suivi.

## Quatre pièges de la norme, mesurés

### La casse de `scormtype` diffère entre les deux normes

SCORM 1.2 écrit `adlcp:scormtype`, SCORM 2004 `adlcp:scormType`. Une comparaison
sensible à la casse ne voit qu'une famille sur deux — et comme le point d'entrée a
un repli, **le paquet se lance quand même** : seul le compte de SCO tombe à zéro,
silencieusement.

### `schemaversion` n'est pas normalisé au caractère près

On relève « 1.2 », « CAM 1.3 », « 2004 3rd Edition », « 2004 4th Edition ». Une
comparaison par égalité classe les trois derniers en SCORM 1.2, et le module lit
alors `cmi.core.lesson_status` sur un contenu qui écrit `cmi.completion_status` :
la complétion ne remonte jamais. On classe donc par appartenance.

### SCORM 2004 a DEUX axes, et ils ne disent pas la même chose

`cmi.completion_status` dit si le contenu a été parcouru, `cmi.success_status` s'il
a été réussi. **Un contenu peut être « completed » ET « failed ».** Ne lire que la
complétion ferait passer un échec pour une formation suivie.

### Ajouter une catégorie de contenu exige son compteur

`_compute_slides_statistics` du natif construit ses clés depuis les valeurs de
`slide_category` puis écrit `channel[cle]`. Une catégorie ajoutée par
`selection_add` sans son champ `nbr_<categorie>` lève `KeyError` au premier flush
qui touche un canal — pas à l'installation, pas aux vues, mais bien plus tard, sur
une écriture sans rapport apparent avec le module.

## Ce qu'il refuse

- **Un paquet dont le manifeste ne se lit pas est refusé au dépôt**, pas accepté et
  cassé au premier clic d'un apprenant.
- **Une tentative sans état terminal ne vaut pas un échec.** `incomplete` et
  `not attempted` restent ce qu'ils sont.
- **Le module ne sert que des fichiers du paquet.** Un chemin qui en sort est
  refusé sous toutes ses formes : `../`, `a/../../`, absolu, ou avec les barres
  obliques inverses de Windows.
- **La durée d'un SCORM n'est pas devinée.** Le natif estime celle d'un document
  par son nombre de pages ; un paquet n'a pas de page, et une durée inventée
  entrerait au registre comme des heures de formation.

## Dépendances

`bf_training_slides` (et donc `bf_training` et `website_slides`).

## Licence

BUSL-1.1. Chaque version bascule en LGPL-3.0-or-later quatre ans après sa
publication. Voir `LICENSE`.
