# Registre de formation : entraînement à la tâche

Le pont entre la cartographie de processus (`bf_process`) et le registre de
formation (`bf_training`), développé par
[Les services de consultation Blue Fox, Inc.](https://symbifox.com)

Une carte dit **ce qui se fait** et **par quel rôle**. Le registre dit **qui doit
savoir le faire**. Entre les deux, il manquait une information que personne ne
détenait.

## Le couloir gagne ses personnes

Une carte nomme un rôle, « Opérateur », « Administration de l'identité », et lui
donne des étapes. Elle ne dit nulle part **qui** tient ce rôle. C'est
exactement ce qui manque pour qu'une exigence de formation puisse viser « tous
ceux qui font ça » plutôt qu'une liste de noms recopiée à la main, qui se périme
au premier départ.

Le pont ajoute les personnes au couloir, et une exigence peut alors prendre la
portée **Un couloir de processus**. Quelqu'un qui entre dans le rôle hérite de
l'exigence ; quelqu'un qui en sort cesse d'en relever.

⚠️ **Un couloir sans personne ne vise personne**, et surtout pas tout le monde
par défaut. La carte nomme le rôle, elle ne sait pas qui le tient : une exigence
qui ne vise personne se lit à sa couverture de zéro sur zéro, ce qui est une
information, alors qu'une portée qui se rabattrait sur l'effectif entier serait
un mensonge.

## L'étape gagne ses formations

Depuis un nœud de la carte, on voit ce qu'il faut savoir pour le tenir, et de là
qui est à jour. Une formation peut couvrir plusieurs étapes, et une étape peut en
demander plusieurs : le lien est un vrai plusieurs-à-plusieurs, parce que la
réalité l'est.

Les **couloirs concernés** par une formation s'en déduisent : ce sont les rôles
qui tiennent au moins une des étapes enseignées.

## La double confirmation

Une formation donnée au poste de travail ne laisse ni facture ni attestation
d'organisme. Ce qui en tient lieu, c'est que **les deux personnes le disent** :
celle qui a montré, et celle qui a appris.

Tant que l'une des deux manque, la réalisation est marquée **incomplète** et sort
des totaux, exactement comme une ligne sans heures. Le formateur est exigé aussi :
un entraînement sans formateur nommé n'a pas eu lieu.

C'est aussi la forme que la carte emploie déjà pour valider une étape, où le
propriétaire et l'exécutant se prononcent chacun. Le pont reprend ce geste plutôt
que d'en inventer un autre.

⚠️ **Les manques du socle survivent.** L'extension ajoute ses exigences, elle
n'efface pas celles d'en dessous : une réalisation sans heures ET sans
confirmation les signale toutes les deux. Un essai le vérifie, parce que c'est
l'erreur naturelle quand on surcharge un calcul.

## Ce qu'il n'invente pas

Le plan de formation reste exigé par le socle pour l'entraînement à la tâche, et
ce pont ne le contourne pas : c'est la condition d'admissibilité de la dépense,
pas une case de plus.

## Essais

11 essais. Quatre mutations éprouvées : la portée par couloir qui viserait tout
le monde, la confirmation de l'apprenant qui ne serait plus exigée, l'extension
qui écraserait les manques du socle, et le formateur devenu facultatif.

```
odoo -d <base> -u bf_training_process --test-enable --test-tags bf_training_process
```

## Licence

Business Source License 1.1, avec passage en LGPL-3.0-or-later le 2030-09-13.
