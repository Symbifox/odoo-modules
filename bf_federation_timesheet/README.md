# Fédération : relevés des heures (`bf_federation_timesheet`)

Entre un sous-traitant et un entrepreneur général, les heures faites sont l'une
des choses les plus évidemment utiles à partager. Ce qu'un partenaire veut
recevoir, ce n'est pas la ligne de temps qui change tous les jours : c'est **le
relevé**, arrêté, pour une période, et dont on sait qu'il a été lu.

## Ce que fait le module

Aucune ligne de temps ne traverse. Le module produit un relevé et le remet comme
un **livrable fédéré** (`bf_federation_document`), qui sait déjà porter une
version et rapporter un accusé de réception.

Depuis le projet, **Remettre le relevé des heures** : la période, le
regroupement (par tâche, par personne ou par jour), avec ou sans les
descriptions. Le relevé part en PDF pour le lire et en CSV pour le reprendre.

## Ce qui ne sort jamais

Les **montants**, les taux, le coût d'une ligne et le solde d'une banque
d'heures. `account.analytic.line` porte le coût (`amount`) juste à côté de la
durée (`unit_amount`) : le relevé ne lit que la durée.

## Un relevé corrigé

Un relevé est identifié par son projet et sa période. Refaire celui d'une période
déjà remise en publie une **nouvelle version** si le contenu a changé, et
l'accusé de la version précédente tombe des deux côtés. Un relevé identique ne
change rien.

Un relevé **reçu** d'un pair porte la référence de son émetteur, et les
identifiants de projet se recoupent d'une instance à l'autre : produire son propre
relevé ne réécrit jamais un livrable reçu.

## Qui peut le remettre

Le rôle de gestionnaire de projet. Le **client** du projet cadre le pair proposé,
comme le destinataire d'un livrable.

## Dépendances

`bf_federation_document`, `hr_timesheet`.
