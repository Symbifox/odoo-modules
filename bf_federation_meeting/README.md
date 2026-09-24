# Fédération : ordres du jour et comptes rendus (`bf_federation_meeting`)

Avec la tâche, l'ordre du jour est le seul objet de la fédération qui vive
vraiment des deux côtés : il se construit avant la rencontre, et les deux
équipes ont des choses à y mettre. Le compte rendu, lui, vient après : il se
lit, et ce qui compte est qu'il soit à jour.

La forme est **asymétrique, et c'est voulu**.

## L'ordre du jour voyage en lecture

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

## Le statut suit l'objet

Confirmé, terminé, **annulé** : l'état de l'ordre du jour chez celui qui anime la
rencontre arrive chez le pair, sans que personne ait à le recopier. L'annulation
est le cas qui compte le plus : rien d'autre ne la signale, puisqu'un ordre du
jour annulé n'est pas archivé chez son émetteur, et personne ne pense à aller
éteindre une rencontre dans l'agenda de quelqu'un d'autre.

L'état voyage par **son propre verbe**, pas par la carte. C'est une règle du
socle : l'empreinte d'une carte ne porte pas l'état, pour qu'un changement de
statut ne fasse pas repartir tout le contenu de l'objet.

## L'envoi de l'émetteur se lit, sans que le receveur prétende avoir envoyé

Un miroir affiche « Envoyé par le pair le … » dans deux champs à lui. Son propre
état d'envoi reste « Non envoyé », qui est vrai chez lui : **il n'a envoyé de
courriel à personne**. Recopier l'état d'envoi de l'émetteur ferait dire au
receveur quelque chose de faux, et dans `bf_meeting` « Envoyé à la main » veut
dire « parti par un autre canal qu'Odoo », ce qui le serait deux fois.

La mise à jour vers 18.0.1.1.0 retire des miroirs existants toute trace d'envoi
qui y aurait été posée à la main (dates d'envoi, « envoyé à la main »). Elle ne
touche pas à leur état, et n'invente pas le repère de l'émetteur : il arrive
avec le premier changement d'état envoyé par le pair.

## Le compte rendu

Le compte rendu voyage avec la charge que `bf_meeting` produit déjà pour son
échange entre locataires : **ce que le PDF montre, et rien d'autre**. À
l'arrivée, elle repasse par le même validateur que s'il s'agissait d'un fichier
joint à un courriel — le réseau n'est pas une source plus sûre qu'une pièce
jointe.

**L'état du miroir reste « Brouillon », toujours.** C'est le seul champ d'état
d'un compte rendu, et c'est un état d'ENVOI. L'état de celui qui a tenu la
rencontre vit dans un champ séparé. Ce refus tient fermée, par construction, la
porte du portail client du receveur : un compte rendu n'y paraît que s'il est
« envoyé », daté et adressé, et un miroir n'est aucun des trois.

**Une copie déjà reçue par courriel est adoptée, pas dupliquée.** Si le receveur
a déjà repris ce compte rendu par l'échange de fichiers, le partage la relie à
son original au lieu d'en créer une seconde, et elle redevient un brouillon en
prenant les règles du miroir.

## Ce qui ne traverse jamais

Les notes en direct, le verbatim, l'état du raffinage, les destinataires du
courriel, la date d'envoi réelle, et tout ce qui touche à la banque d'heures.

## Dépendances

`bf_federation`, `bf_meeting`.
