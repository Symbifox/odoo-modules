# Registre de formation : accusé signé par module

Le pont entre la base de connaissances versionnée (`project_knowledge_matrix`) et
le registre de formation (`bf_training`), développé par
[Les services de consultation Blue Fox, Inc.](https://symbifox.com)

Certaines formations ne sont pas des cours : ce sont des **documents à lire**, et
la preuve attendue est un accusé daté, parfois signé, versé au dossier de la
personne. Une politique de prévention, un processus d'accueil en plusieurs
modules, une procédure qui change.

La maison sait déjà distribuer un document versionné et recueillir un accusé. Ce
pont fait que cet accusé **compte comme une formation suivie**.

## Ce qu'il fait

- **Une activité s'adosse à un document.** Distribuer une version à quelqu'un,
  c'est lui assigner la formation.
- **L'accusé écrit la réalisation**, datée du moment où il arrive, et rattachée à
  **la version lue**.
- **Une nouvelle version rouvre l'obligation** de ceux qui n'ont accusé que
  l'ancienne.

## Deux refus assumés

### Une signature exigée est exigée pour de vrai

Quand la remise demande une signature et qu'elle manque, **rien n'est consigné**.
Une case cochée ne remplace pas un document daté et signé, et c'est précisément
ce que la réglementation demande de verser au dossier.

⚠️ **Et la signature qui arrive après coup débloque l'écriture.** Sans ce
crochet, une remise accusée puis signée resterait sans ligne au registre :
l'accusé serait déjà passé, et rien ne repasserait jamais. C'est le genre de trou
qu'on ne voit pas avant d'avoir un dossier vide devant un inspecteur.

### Une personne sans fiche d'employé n'obtient pas une fiche fabriquée

Le registre est un registre du personnel. Un accusé venu de quelqu'un hors
effectif est journalisé et ne produit rien.

## La péremption se lit sur les versions

Le socle compare deux numéros de version qu'il gère lui-même. Ici la vérité est
ailleurs : c'est la **version en vigueur du document** qui dit ce qui vaut.
Fabriquer un entier à côté aurait donné deux sources pour un même fait, et elles
auraient divergé au premier document repris à la main.

Une activité adossée à un document est donc livrée avec la **rouverture au
changement de version activée par défaut** : un document qui change doit être
relu. C'est un défaut, pas une option à trouver.

## Un module par module

Quand un processus d'accueil comporte plusieurs modules, chacun a son document,
donc sa propre activité, sa propre remise et son propre accusé. C'est la forme
que la réglementation demande, et c'est aussi la seule qui permette de dire
**lequel** manque.

## Essais

11 essais. Six mutations éprouvées : la signature exigée qui passerait quand
même, la signature d'après coup qui ne débloquerait plus rien, la remise qui
écrirait deux fois, l'accusé d'une vieille version resté valable, la rouverture
décochée ignorée, et la fiche d'employé fabriquée pour une personne hors
effectif.

```
odoo -d <base> -u bf_training_sign --test-enable --test-tags bf_training_sign
```

## Licence

Business Source License 1.1, avec passage en LGPL-3.0-or-later le 2030-09-13.
