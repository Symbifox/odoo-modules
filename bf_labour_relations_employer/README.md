# Relations de travail : côté employeur (`bf_labour_relations_employer`)

Le socle décrit ce que la relation **est**. Ce greffon porte ce que l'employeur
**doit faire**, et quand.

## Les cinq objets

| Modèle | Ce qu'il porte |
|---|---|
| `bf.labour.obligation` | Ce que la convention impose, avec échéance, récurrence et rappel |
| `bf.labour.seniority.list` (+ `.line`) | La liste d'ancienneté **affichée**, figée |
| `bf.labour.posting` (+ `.bid`) | Affichages, supplantations, rappels, et les candidatures classées |
| `bf.labour.committee` (+ `.meeting`) | Le comité de relations de travail et sa cadence |
| extension de `bf.labour.dues.remittance` | L'assiette saisie et la règle appliquée dessus |

## 🔴 La liste affichée est une PHOTO, pas une vue

C'est la décision qui porte tout le module. L'appartenance vit et change ; la
liste affichée le 1er mars, elle, est celle **contre laquelle les rangs se
contestent**. Chaque ligne porte donc sa propre `seniority_date`, recopiée au
moment de la composition, jamais lue en direct depuis l'appartenance.

Une liste qui se recalculerait donnerait raison rétroactivement à l'employeur
dans chaque grief de rang : la correction d'ancienneté faite après coup
effacerait l'erreur qu'on conteste.

Une liste affichée ne se recompose plus et ses lignes ne se récrivent plus. Une
erreur se corrige en **affichant une liste corrigée et datée**, ce qui laisse la
trace de la correction. Seul le drapeau « contesté » reste ouvert : contester
n'est pas récrire.

## 🔴 L'octroi hors rang n'est pas interdit, il est obligé de s'écrire

Un employeur a le droit d'écarter la personne la plus ancienne. Il n'a pas le
droit de le faire sans raison. `action_award()` bloque donc l'octroi à une
candidature moins ancienne **tant que `award_reason` est vide**, et c'est ce
texte qu'on relira en grief.

Les candidatures retirées ou jugées non admissibles ne comptent pas dans le
calcul : une personne qui s'est retirée n'a pas été sautée. Et écarter une
candidature demande elle aussi un motif écrit.

Comme pour la liste, **le rang d'une candidature est recopié** : une candidature
de mars se juge avec les rangs de mars.

## ⚠️ Le rappel, et pourquoi il porte un drapeau

Une obligation dont personne n'est averti est une obligation manquée. Un cron
quotidien pose une `mail.activity` sur la personne responsable, `reminder_days`
avant l'échéance.

Le filtre porte sur `reminder_posted`, **pas sur l'existence d'une activité** :
une activité que quelqu'un a annulée à la main ne doit pas revenir à chaque
passage, sinon le rappel devient du bruit qu'on cesse de lire.

Un `reminder_days` à zéro veut dire « aucun rappel », pas « rappel le jour même ».

Une obligation récurrente **crée** la suivante au lieu de se déplacer : déplacer
effacerait l'historique de conformité au premier passage, et c'est précisément
cet historique qu'un syndicat ressort.

## La remise : l'assiette est saisie, la règle est appliquée

Le module ne devine pas l'assiette, il l'attend. Ce qu'il garantit, c'est que la
règle de la convention est appliquée **uniformément**, ce qui est exactement ce
qu'une vérification syndicale contrôle.

🔴 `_applicable_rule()` prend la règle **en vigueur à la fin de la période**, pas
la dernière saisie. Un taux qui change en cours de convention laisse deux règles
datées ; prendre la plus récente donnerait le mauvais montant sur toute période
antérieure au changement, et ce genre d'erreur ne se voit qu'à la vérification.

L'écart entre le saisi et le calculé est **montré, jamais corrigé en silence** :
l'effacer supprimerait ce qu'une vérification cherche. `action_apply_rule()` est
un geste explicite, et il est refusé sur une remise déjà déclarée.

## ⚠️ Ce qui est public, et ce qui ne l'est pas

La liste **affichée** est lisible par tout le personnel de la société : c'est un
affichage, et sans ça il n'y a pas de contestation possible. Une liste encore en
brouillon ne l'est pas, sinon on afficherait avant d'avoir affiché.

Les **candidatures**, elles, ne sont pas publiques. Savoir qui a postulé sur quoi
se retourne contre les gens, et la convention n'en fait pas un affichage. Chacune
voit la sienne, l'administration voit toutes.

## Essais

40 essais. Éprouvés par mutation : faire lire la liste affichée en direct depuis
l'appartenance, supprimer l'exigence du motif d'octroi, et prendre la dernière
règle de cotisation au lieu de celle de la période font tomber trois essais
nommément.
