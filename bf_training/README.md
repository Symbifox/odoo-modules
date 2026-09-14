# Registre de formation

Le registre nominatif de la formation du personnel, pour Odoo 18, développé par
[Les services de consultation Blue Fox, Inc.](https://symbifox.com)

Un module eLearning est un **lecteur** : il joue des contenus et sait qui a
cliqué. Ce module est un **registre** : il tient qui doit quoi, pour quand,
prouvé par quelle pièce, et valide jusqu'à quand. Les deux se complètent, et
aucun ne remplace l'autre.

## Ce qu'il porte

- **Activité** : le catalogue. Un titre, une catégorie, un mode, une durée
  prévue, un organisme, une validité en mois.
- **Exigence** : la règle. Qui doit quoi (une activité précise, ou un nombre
  d'heures dans des catégories, avec des catégories exclues), à partir de quel
  déclencheur, avec quel délai, à quelle fréquence, et sur quelle base
  réglementaire citée au long.
- **Obligation** : la règle appliquée à une personne. Une ligne par personne
  visée, avec son échéance, son état et la pièce qui la couvre. Fabriquée et
  tenue à jour par une tâche planifiée.
- **Réalisation** : la preuve. La vraie date, les heures, l'organisme, le
  numéro, la pièce jointe reçue de l'extérieur, la date d'expiration, et le
  coût.
- **Plan de formation** : la durée établie à l'avance et la preuve de la
  consultation tenue sur le plan.
- **Assignation** : le geste. Demander, et relancer avant l'échéance.

## Trois principes de conception

Chacun corrige un défaut observé dans ce qui existe.

### 1. Un état qui dépend de la date du jour se porte par une tâche planifiée

Un champ calculé **stocké** qui ne dépend que d'une date de fin ne se recalcule
jamais parce que le temps a passé : il affiche « valide » pour l'éternité. C'est
un mensonge qui ne se voit pas le jour où on l'écrit, mais deux ans plus tard,
sur un écran que personne ne rouvre.

Ici, `expiry_state` n'est **pas** un champ calculé. Il est écrit à
l'enregistrement et réécrit chaque jour par
`bf.training.record._cron_refresh_expiry()`. Même chose pour l'état des
obligations, porté par `bf.training.obligation._cron_refresh()`.

### 2. Une réalisation ne s'écrase pas

Chaque renouvellement est une ligne de plus. Trois secourismes en neuf ans
laissent trois lignes, et la suite se montre. Un registre qui garde une seule
ligne par couple (personne, formation) ne peut pas prouver une continuité.

### 3. Ce qui manque ne vaut pas zéro

Une réalisation sans heures ou sans coût horaire est marquée **incomplète** et
sort des totaux, au lieu d'y entrer pour une valeur nulle qui ressemble à une
mesure. Le champ `missing_info` dit ce qui manque, en toutes lettres.

## Deux formes d'exigence, et pourquoi les deux

Certaines obligations portent sur une **activité précise** que chaque personne
visée doit avoir suivie et gardée valide. D'autres portent sur un **nombre
d'heures par catégorie**, avec des catégories qui ne comptent pas. Les deux ne
se réduisent pas l'une à l'autre, et une exigence en heures sans catégories
exclues ne sait pas dire « six heures par an, et le secourisme n'en fait pas
partie ».

Une exigence en heures compte sur une période : une fois, par année civile, ou
sur une période glissante.

## L'ancrage des échéances

| Déclencheur | Point de départ |
|---|---|
| Embauche | `training_reference_date` de l'employé, plus le délai |
| Date fixe | la date inscrite sur l'exigence, plus le délai |
| Immédiat | la date d'écriture de l'exigence, plus le délai |

⚠️ Le déclencheur « immédiat » s'ancre à l'écriture de la règle, **pas au jour
du calcul**. Une échéance qui avance avec le calendrier n'est jamais dépassée, et
ne mesure donc rien.

⚠️ Une personne sans date de référence n'obtient **pas** une échéance inventée :
son obligation reste « sans échéance ». Une date fausse a l'air d'une mesure ;
une date absente se voit.

## La date de référence

Le module `hr` de base ne porte aucune date d'embauche : `first_contract_date`
n'existe qu'avec les contrats et reste réservé aux gestionnaires RH. Le registre
pose donc `training_reference_date` sur l'employé, reprise du premier contrat
quand il existe, sinon de la création de la fiche, et **modifiable à la main**.

## Tâches planifiées

| Tâche | Ce qu'elle fait | Par défaut |
|---|---|---|
| Rafraîchir les validités | réécrit `expiry_state` sur toutes les réalisations datées | **active** |
| Rafraîchir les obligations | recrée et réévalue les lignes d'obligation | **active** |
| Relances | relance les assignations qui approchent de l'échéance | **inactive** |

⚠️ La tâche de relance est livrée **décochée**. Une relance automatique part vers
des personnes réelles : l'état sûr est celui qui n'envoie rien, et c'est à
l'exploitant de l'armer quand il a vérifié à qui elle parlerait. Le préavis se
règle par `bf_training.reminder_days` (7 jours par défaut) et l'échéance par
défaut d'une assignation par `bf_training.default_due_days` (30 jours).

La relance part sous le nom de la société et porte un bouton **Ouvrir la
formation**. Par défaut il mène à la fiche de l'assignation ; un module qui adosse
l'activité à un cours peut le faire mener au cours. L'échéance s'écrit en toutes
lettres dans la langue de la personne.

Elle se range avec les autres courriels de la société :

| La base porte | Mise en page | Couleur du bouton | Signature |
|---|---|---|---|
| `bluefox_branding` | celle de la marque (`bf_mail_layout`) : bandeau, accent, police et pied de la société | l'accent de marque de la société | le nom de la société, sauf si elle a une signature par défaut, que la mise en page pose déjà |
| rien de plus | la mise en page légère d'Odoo | la couleur des boutons de courriel de la société | le nom de la société |

`bluefox_branding` n'est pas une dépendance : la relance le détecte à l'envoi. Aucune
couleur n'est écrite dans le gabarit, et aucune n'est assombrie : le bouton emploie
la couleur que sa mise en page emploie elle-même.

⚠️ **Les montées vers 18.0.1.1.0 et 18.0.1.2.0 remplacent le gabarit de relance**,
même protégé par `noupdate` : son corps a changé, et Odoo ne réécrit jamais un
enregistrement protégé. Un gabarit retouché à la main est donc remplacé par ces
montées ; reportez vos retouches après.

## Droits

Deux groupes : **Agent** tient le registre (saisit, assigne, relance), et
**Responsable** écrit les exigences et les plans. Toute personne connectée voit
**ses** réalisations, ses obligations et ses assignations, et rien de celles des
autres.

## Ce que le module ne fait pas

Il ne joue aucun contenu, il ne produit pas encore d'attestation en PDF, il ne
calcule pas le relevé de la participation au développement des compétences, et
il ne se raccorde pas encore à l'eLearning d'Odoo. Ces morceaux vivent dans des
modules séparés qui s'appuient sur celui-ci.

## Essais

25 essais, et six mutations sur six attrapées : l'expiration qui ne périme plus,
la dispense défaite par le recalcul, l'exclusion de catégorie ignorée, l'échéance
ancrée au mauvais jour, la ligne incomplète comptée pour complète, et la fenêtre
annuelle ouverte sur toute l'histoire.

```
odoo -d <base> -u bf_training --test-enable --test-tags bf_training
```

## Licence

Business Source License 1.1, avec passage en LGPL-3.0-or-later le 2030-09-13.
Voir `LICENSE`.
