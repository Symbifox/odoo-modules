# Registre de formation : ce qu'elle coûte vraiment

Le pont entre le **budget d'exploitation** et le registre de formation, pour
Odoo 18, développé par
[Les services de consultation Blue Fox, Inc.](https://symbifox.com)

Le registre sait déjà ce qu'une formation a coûté : les heures, le taux horaire,
les cotisations de l'employeur, les frais. Le budget sait ce qu'on avait prévu.
Ce pont met les deux en face.

## Ce qu'il fait

- **Une ligne budgétaire peut se nourrir du registre.** On y nomme des catégories
  de formation, et le réalisé de la ligne prend le coût des formations suivies
  dans la période — sans écriture comptable, puisqu'un coût interne n'en produit
  pas.
- **L'engagé prend les obligations à venir.** Ce qui est dû et pas encore suivi a
  un coût prévisible : heures exigées multipliées par le taux horaire de la
  personne. Il apparaît comme engagé, pas comme une surprise de fin d'exercice.

## Un refus repris, parce que les deux modules le partagent déjà

Le budget porte `unvalued_hours` : « heures saisies dont le coût est nul ».
Sa docstring dit exactement pourquoi :

> Quand ce taux n'est pas renseigné, le montant vaut 0,00 $ alors que les heures,
> elles, sont bien là. La ligne afficherait « rien dépensé » et personne ne
> saurait que c'est un réglage qui manque.

Le registre porte `is_complete` et `missing_info`, qui disent la même chose. Ce
pont les branche l'un sur l'autre plutôt que d'inventer un troisième vocabulaire :

- une formation **complète** entre dans le réalisé ;
- une formation **incomplète** n'y entre pas, et ses heures s'ajoutent aux heures
  non valorisées de la ligne, là où le budget les signale déjà ;
- une obligation dont on ne sait pas chiffrer le coût — activité sans durée, ou
  personne sans taux — reste **dehors** de l'engagé. Un engagement sous-évalué est
  pire qu'un engagement absent, parce qu'il a l'air renseigné.

## La garde du socle, étendue et non desserrée

`bf_budget` refuse une ligne de coût interne sans compte analytique : « c'est son
seul axe ». Le refus est juste — sans axe, la ligne ne peut rien sélectionner,
lit zéro pour toujours, et a l'air parfaitement normale.

Ce pont ne change pas cet invariant. Il ajoute un axe : **une catégorie de
formation en est un**, et elle sélectionne aussi précisément qu'un compte
analytique. Une ligne sans analytique **et** sans catégorie lève toujours.

## Dépendances

`bf_training`, `bf_budget`.

## Licence

BUSL-1.1. Chaque version bascule en LGPL-3.0-or-later quatre ans après sa
publication. Voir `LICENSE`.
