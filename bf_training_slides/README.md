# Registre de formation : raccord eLearning

Le pont entre le lecteur de contenus d'Odoo (`website_slides`) et le registre de
formation (`bf_training`), développé par
[Les services de consultation Blue Fox, Inc.](https://symbifox.com)

## Ce qu'il fait

- **Une activité du registre s'adosse à un cours.** L'inscription, l'avancement
  et la complétion suivent, dans les deux sens.
- **La complétion écrit une réalisation datée.** Au registre, avec les heures,
  le mode, et la version du contenu suivie.
- **La dérive du contenu se voit**, sans que rien ne soit décidé à la place de
  la personne.
- **Le compte portail se fabrique au besoin**, parce que le cours n'enregistre
  la complétion que pour une personne connectée.
- **La relance mène au cours.** Le bouton du courriel de relance ouvre le cours
  adossé à l'activité, plutôt que la fiche de l'assignation.

## Trois défauts du natif, et ce qu'on en fait

### 1. Il n'existe aucune date de complétion

Ni `slide.channel.partner` ni `slide.slide.partner` ne portent de date. La ligne
de CV que produit `hr_skills_slides` est datée du jour où le calcul tourne, pas
du jour où la personne a fini.

**Ici** : la réalisation est écrite **au moment où la complétion se produit**,
dans `_recompute_completion`. La date veut donc dire quelque chose.

### 2. Un membre passé à « complété » y reste, même quand le cours change

`website_slides/models/slide_channel.py` saute les membres `completed` au
recalcul, et l'assume : « once completed, membership should remain so ». Ajouter
soixante contenus à un cours de conformité ne rouvre rien pour personne.

**Ici** : le nombre de contenus publiés est figé au moment où la version de
l'activité est fixée. S'il change, l'activité est marquée **dérivée**. Deux
gestes s'offrent alors, et **c'est une personne qui tranche** :

- *Monter la version* : ceux qui ont suivi l'ancienne devront refaire.
- *Prendre acte* : le compte est figé sans rien redemander.

⚠️ Le module ne monte **jamais** la version tout seul. Corriger une coquille ne
doit faire refaire le cours à personne, et un registre qui redemande une
formation à chaque retouche se fait ignorer en trois semaines.

### 3. La complétion n'est enregistrée que pour une personne connectée

Sans compte, l'inscription tient mais rien ne revient. Le raccord fabrique un
compte portail quand c'est permis (`bf_training.auto_grant_portal`, actif par
défaut) et qu'il y a une adresse.

## Ce qu'il refuse de faire

**Fabriquer une fiche d'employé.** Une personne qui complète un cours sans
dossier d'employé ne produit aucune ligne au registre, et c'est journalisé. Le
registre est un registre du personnel ; inventer un employé pour y loger une
complétion en ferait autre chose.

## Essais

8 essais. Deux mutations éprouvées : la réalisation qui se dédouble à chaque
passage, et la fiche d'employé fabriquée pour une personne hors effectif.

```
odoo -d <base> -u bf_training_slides --test-enable --test-tags bf_training_slides
```

## Licence

Business Source License 1.1, avec passage en LGPL-3.0-or-later le 2030-09-13.
