# Fédération : cartographies (`bf_federation_process`)

Une carte savait déjà traverser : `bf_process` exporte en BPMN 2.0 et le relit
avec une garantie de fidélité, et un fichier se recopie d'une instance à l'autre
en quelques minutes. Ce que la copie n'a pas, c'est **le fil**. Les deux
exemplaires sont figés au moment du transfert, et rien ne dit à celui d'en face
qu'une version a suivi.

## Ce qui traverse

La forme d'échange de `bf_process`, celle que le module emploie déjà pour une
nouvelle version et pour une cible : niveaux, couloirs, pools externes, nœuds,
flux et messages. Le miroir est une **vraie** cartographie chez le pair, qui
trace son PDF, ouvre ses pages d'étape et se lit dans le visualiseur.

Les codes de niveau et les identifiants BPMN voyagent explicitement : sans eux,
le miroir renumérote ses niveaux et tout ce qui s'accroche à un code (pages
d'étape du portail, codes QR, calcul des écarts) pointerait à côté.

## Le miroir se lit

Une carte reçue ne se retouche pas : la version suivante la remplacerait en bloc
et le travail serait perdu. L'écriture est refusée avec le motif.

## Renvoyer le tracé

Les niveaux d'une carte ne sont pas des champs surveillés, et une carte se
retouche par ses nœuds bien plus souvent que par son titre. Le bouton **Renvoyer
le tracé au pair** envoie la carte telle qu'elle est.

## Les noms

Une carte reçue porte le nom de son pair (« Cycle client (Untel) ») : `bf.process`
impose l'unicité de (nom, nature, version), et deux maisons qui cartographient le
même métier appellent leur carte pareil.

## Ce qui reste chez l'émetteur

Le registre de validation, la prose du livrable, les gels et les écarts.

## Dépendances

`bf_federation`, `bf_process`.
