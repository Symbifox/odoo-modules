# Registre de formation : Québec

La couche québécoise du registre de formation, développée par
[Les services de consultation Blue Fox, Inc.](https://symbifox.com)

Ce que le droit québécois demande d'un registre de formation, et que le registre
générique ne porte pas : l'attestation, la conservation, le relevé, et les
références citées au long.

## L'attestation de formation

L'employeur doit être en mesure de délivrer annuellement une attestation à tout
employé ayant participé à une formation qu'il a donnée lui-même, à défaut pour
l'établissement, l'organisme ou le formateur d'en délivrer une précisant l'objet
de l'activité.

Le PDF porte l'objet, la date **de la formation**, la durée, le mode, le
formateur, le numéro, la validité, le plan, et la personne morale **de la
réalisation**.

Les dates s'écrivent en toutes lettres (« 12 mai 2026 ») : « 05/12/2026 » se lit
mai ou décembre selon qui le lit, et une attestation est faite pour être lue par
un tiers.

⚠️ Deux détails qui ne sont pas des détails, et que le certificat natif d'Odoo
rate tous les deux :

- **La date est celle de la formation**, pas celle de la saisie. Le certificat
  de `survey` imprime `user_input.create_date`, c'est-à-dire souvent le jour de
  l'invitation. Un examen invité le 18 décembre et réussi le 6 janvier sort daté
  de l'année précédente, pour une dépense qui se déclare par année civile.
- **L'employeur est celui de la réalisation.** Le certificat natif nomme
  `user_input.create_uid.company_id`, la société de qui a créé
  l'enregistrement : ni l'employé, ni le cours. Sur un locataire qui porte
  plusieurs personnes morales, il nomme la mauvaise.

Quand la durée n'est pas consignée, l'attestation le **dit**. Elle n'imprime pas
zéro heure.

## La conservation de six ans

Les pièces justificatives se conservent six ans après la dernière année à
laquelle elles se rapportent. La date de fin est calculée depuis la fin de
l'année civile de la réalisation, et **la suppression d'une ligne encore
couverte est refusée**.

Annuler la réalisation la retire des totaux sans la faire disparaître : c'est le
geste prévu quand une ligne est fausse.

## Le relevé de participation

La masse salariale, le seuil, la participation minimale, les dépenses
admissibles, l'excédent reporté, et s'il y a lieu la cotisation à verser.

Le salaire admissible se calcule en **heures multipliées par le taux horaire**,
augmenté des cotisations de l'employeur. C'est le socle qui porte ces trois
nombres ; cette couche ne fait que les additionner et les confronter au minimum.

⚠️ **Le seuil s'excède, il ne s'atteint pas.** Une masse salariale exactement
égale au seuil n'assujettit pas. Le seuil et le taux sont des **paramètres**
(`bf_training_qc.payroll_threshold`, `bf_training_qc.participation_rate`), pas
des constantes du code : ils bougent par règlement.

### Trois refus assumés

1. **Une réalisation incomplète ne compte pas, et n'est pas comptée pour zéro.**
   Elle passe dans une liste à part, avec ce qui lui manque, et ses heures sont
   totalisées séparément. Un relevé qui avale les trous rend un chiffre faux qui
   a l'air juste.
2. **Une activité dont l'admissibilité n'est pas établie ne compte pas non
   plus.** Le module ne devine pas la base d'admissibilité : il la demande.
3. **Le relevé n'est pas la déclaration.** Il l'appuie. L'écran et le document
   le disent, et le chiffre reste à valider par qui produit la déclaration.

## Les références réglementaires

Un catalogue de bases réglementaires, citées au long avec leur texte, qu'une
exigence peut reprendre au lieu de les recopier de mémoire. Onze références sont
livrées, lues sur LégisQuébec dans leur version en vigueur au 2026-09-13 :
participation et seuil, entraînement à la tâche, justification et conservation,
calcul du salaire, formation et entraînement en santé et sécurité, politique de
harcèlement psychologique, secourisme et perfectionnement en services de garde,
accueil et formations obligatoires en résidence privée pour aînés.

⚠️ Ce catalogue est un point de départ documenté, pas un avis juridique. Les
textes changent ; la date de lecture est inscrite dans les données.

## Essais

24 essais. Six mutations éprouvées : la ligne incomplète comptée, l'activité sans
base comptée, l'écartée entrée au relevé pour zéro, la conservation qui ne retient
plus rien, le seuil qui s'atteint au lieu de s'excéder, et l'attestation qui nomme
la société de qui imprime.

```
odoo -d <base> -u bf_training_qc --test-enable --test-tags bf_training_qc
```

## Licence

Business Source License 1.1, avec passage en LGPL-3.0-or-later le 2030-09-13.
