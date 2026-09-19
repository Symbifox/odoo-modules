# Relations de travail : côté syndical (`bf_labour_relations_union`)

Le pendant du greffon employeur. Le socle décrit ce que la relation **est** ;
celui-ci porte ce que le syndicat **fait**.

## Les cinq objets

| Modèle | Ce qu'il porte |
|---|---|
| `bf.labour.card` | L'adhésion, avec signature, retrait et réadhésion |
| `bf.labour.assembly` (+ `.vote`) | Les assemblées, les présences, les votes |
| `bf.labour.delegate` (+ `.release`) | Les délégués et leur banque de libérations |
| `bf.labour.dues.receipt` | Ce qui a été reçu, rapproché de ce qui a été déclaré |
| extension de `bf.labour.grievance` | Le mandat syndical sur le grief du socle |

## 🔴 Le droit de vote suit l'ADHÉSION. Miroir exact de la cotisation

C'est la raison d'être des deux états séparés du socle, prise par l'autre bout.

| | suit | jamais |
|---|---|---|
| **La cotisation** (socle, greffon employeur) | `covered` | `is_member` |
| **Le droit de vote** (ce greffon) | `is_member` | `covered` |

Une personne couverte qui n'a pas adhéré **paie et ne vote pas**. Si ces deux
chiffres coïncidaient toujours, les deux états ne serviraient à rien.

Le **quorum** se mesure donc sur les membres présents, pas sur l'effectif
couvert. Le calculer sur les couverts le rendrait systématiquement inatteignable
là où beaucoup de gens cotisent sans adhérer.

Et **adhérer ne change pas la couverture** : la couverture vient de
l'accréditation, jamais d'une carte. Une carte qui écrirait `covered`
mélangerait les deux états et la cotisation se mettrait à suivre l'adhésion.

## ⚠️ L'abstention n'entre pas dans l'assiette de la majorité

`share_for` se calcule sur `pour + contre`, pas sur le total des présents.
Inclure les abstentions fait échouer des votes que l'assemblée a adoptés, et
c'est l'erreur de calcul classique sur un mandat de grève.

Les résultats se **saisissent** : un vote se tient au scrutin secret ou à main
levée, et prétendre le dépouiller dans Odoo ferait croire à une traçabilité
individuelle que personne ne veut. Ce qui est enregistré, ce sont les totaux, la
question et le seuil. Une garde refuse de compter plus de votes que de présences
en droit de voter : c'est elle qui attrape la saisie faite de mémoire une
semaine plus tard.

## 🔴 L'écart des cotisations est le seul chiffre qui compte

`bf.labour.dues.receipt` existe **pour le rapprochement**. Enregistrer le reçu
sans le comparer au déclaré donnerait un registre comptable de plus.

Trois chiffres se répondent : ce que le syndicat a reçu, ce que l'employeur
déclare avoir remis, et ce que le socle sait de l'unité. Un relevé qui compte
moins de monde que l'unité n'en couvre mérite une question même si le montant
balance.

Un relevé **sans remise liée n'est jamais « rapproché »**. Le dire rapproché
parce que l'écart calcule zéro serait le pire des faux verts : l'absence de
comparaison se lirait comme une comparaison réussie.

Un écart n'est pas une accusation. Un décalage de période, une personne
rattachée en retard ou un taux changé en cours de période en produisent aussi.
Le modèle le montre, il ne le juge pas.

## Les libérations : c'est le solde qui se conteste

Ce qui se conteste n'est pas le principe, c'est **combien il en restait le jour
du refus**. D'où une banque datée plutôt qu'un compteur.

🔴 Une libération **refusée** compte pour zéro heure mais reste au dossier :
c'est elle qu'on ressort en grief, et la supprimer effacerait le refus. Refuser
exige un motif écrit. Dépasser la banque est **signalé, pas bloqué** : ça arrive,
et c'est justement ce qu'il faut voir.

## Le grief : des champs, jamais un second modèle

Le mandat syndical, la personne-ressource, la décision d'arbitrage et son coût
estimé vivent sur **le grief du socle**. Le jour où les deux côtés cessent de
lire le même dossier, l'outil arrête de servir à ce pour quoi il existe.

Ne pas soutenir un grief exige un motif écrit : le devoir de représentation se
juge sur ce texte, et l'absence de motif est ce qui se retourne contre le
syndicat. Un grief non soutenu reste au dossier, parce que c'est un refus et non
une disparition.

Renvoyer à l'arbitrage **date le mandat** : sans cette date, le dossier n'a plus
la pièce qui prouve que la décision a été prise, et quand.

## Essais

35 essais. Éprouvés par mutation : faire suivre le droit de vote par la
couverture, inclure les abstentions dans l'assiette de la majorité, et faire
écrire `covered` par la carte font tomber quatre essais nommément.

⚠️ Deux de ces essais ne mordaient pas au premier jet, parce que le décor rendait
la mutation invisible : la personne testée était déjà couverte, et les deux
compteurs tombaient par hasard sur le même total. Le décor part maintenant d'une
personne non couverte et de deux totaux différents.
