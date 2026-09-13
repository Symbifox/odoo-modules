# Fédération : ordres du jour (`bf_federation_meeting`)

Avec la tâche, l'ordre du jour est le seul objet de la fédération qui vive
vraiment des deux côtés : il se construit avant la rencontre, et les deux
équipes ont des choses à y mettre.

La forme est **asymétrique, et c'est voulu**.

## Il voyage en lecture

Le pair reçoit le titre, la date, le lieu, la durée, les objectifs, le contexte
et les sujets publiés, dans l'ordre. Le miroir ne se réécrit pas : la passe de
raffinage repasse dessus chez l'émetteur, et deux plumes sur le même objet
fabriquent des conflits. Un bandeau le dit sur le formulaire, et l'écriture est
refusée avec le motif.

## Un seul geste revient : proposer un sujet

Bouton **Proposer un sujet** sur un ordre du jour reçu. Le sujet arrive chez
l'émetteur en « Proposé par un destinataire », à examiner, et il n'entre ni dans
le PDF ni dans le courriel tant que personne ne l'a accepté. C'est exactement
l'état que `bf_meeting` prévoyait déjà pour les propositions venues du lien
public, servi là où le pair travaille.

Un sujet accepté chez l'émetteur entre dans la carte et paraît chez le pair au
prochain envoi. Un sujet proposé par le pair sur son miroir y reste tant qu'il
n'est pas accepté.

## Ce qui ne traverse jamais

Les notes en direct, le verbatim, l'état du raffinage, les destinataires du
courriel, et tout ce qui touche à la banque d'heures.

## Dépendances

`bf_federation`, `bf_meeting`.
