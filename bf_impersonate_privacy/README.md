# Incarnation — pont vie privée (`bf_impersonate_privacy`)

Pont entre `bf_impersonate` et `privacy_consent`. **Pas** `auto_install` : une
durée de conservation est une décision de l'entreprise qui fait tourner
l'instance, inscrite à SON registre, donc le pont s'installe à la main,
locataire par locataire. Propriétaire sous BUSL-1.1, comme les autres ponts
`*_privacy`, là où `bf_impersonate` lui-même est LGPL.

Issu de la tâche BF ** ** (« Journal d'incarnation à améliorer »), où le
trou s'est vu en ouvrant le dossier : la demande portait sur la lisibilité du
journal, pas sur sa conservation.

## Le constat

Le journal des incarnations dit qui a vu Symbifox en tant que qui, pourquoi,
pendant combien de temps, ce qui a été changé, ce qui a été consulté, le nom que
portaient les fiches touchées, et depuis quelle adresse IP. Ce sont des
renseignements personnels sur deux personnes à la fois : celle qui est vue et
celle qui regarde.

Mesuré sur la production de Blue Fox le **2026-11-08** : aucune des **35**
finalités du registre ne visait l'incarnation, aucun des **26** calendriers de
conservation ne s'appliquait au journal, et **rien ne le purgeait**. Les **17**
autres ponts `*_privacy` existaient déjà ; celui-là manquait.

## Ce que le pont livre

| | |
|---|---|
| **Finalité** | `impersonation_journal` — surveiller et pouvoir rendre compte d'un accès administratif au compte d'une personne. Sans consentement. |
| **Conservation** | `INC-001` (le détail, 1 an) et `INC-002` (la séance, 3 ans). |
| **Purge** | Un cron quotidien, **livré actif**, qui efface pour vrai. |
| **Registre** | Une entrée de destruction par règle exécutée et par société, pas une par fiche. |

## Deux durées, parce que les deux moitiés ne prouvent pas la même chose

`INC-001`, **le détail** (les lignes) : ce qui a été changé, ce qui a été
consulté, les identifiants et les noms des fiches. C'est le plus révélateur du
journal (les sujets de courriels d'une personne y passent) et le plus
périssable : il sert à répondre « il a fait quoi, chez moi ? » dans les semaines
qui suivent. **Un an**, aligné sur `AGD-001`, la seule autre règle maison qui
vise un journal d'accès.

`INC-002`, **la séance** (l'en-tête) : qui, en tant que qui, pourquoi, quand,
combien de temps, comment ça a fini, et l'adresse IP. C'est la trace qu'un
pouvoir a été exercé, et elle doit survivre au détail. **Trois ans**, aligné sur
`COR-001`. Purger les deux ensemble effacerait le fait même qu'une séance a eu
lieu.

Le détail est ancré sur la **fin de la séance**, pas sur la date de la ligne :
une séance se lit d'un bloc, et un détail qui part avant son en-tête laisserait
un résumé qui ne résume plus rien.

## Pourquoi la purge efface au lieu de proposer

Les ponts qui visent de la correspondance (`bf_sms_privacy`, `bf_email_privacy`)
déposent une campagne laissée **en révision** : leur destruction est
irréversible pour l'entreprise, et c'est au responsable de la vie privée
d'approuver. Ici, ce sont nos propres traces d'exploitation, dont la durée est
justement la décision déjà prise au registre. Même choix que le ménage de
`bf_editorial_audience_privacy`, et pour la même raison : un journal d'accès que
personne ne purge est le manquement, pas la purge.

## Ce qui l'arrête

Trois cas, et dans les trois la purge ne fait rien plutôt que de détruire en
silence :

- la règle est archivée ;
- sa disposition finale n'est plus la destruction ;
- quelqu'un lui a mis « approbation requise » (la purge le journalise en
  avertissement, pour que le changement ne passe pas inaperçu).

⚠️ Une séance **ouverte** n'est jamais purgée, même échue : c'est le cron de
`bf_impersonate` qui la ferme, et une séance sans fin n'a pas d'ancrage.

## Installation

À la main, locataire par locataire : `-i bf_impersonate_privacy`. Le pont
suppose `privacy_consent` installé (le registre, les finalités, les calendriers
et le registre de destruction).

## Changelog

- **18.0.1.0.0** : première version. Finalité, `INC-001`, `INC-002`, purge
  quotidienne et inscription au registre de destruction.
