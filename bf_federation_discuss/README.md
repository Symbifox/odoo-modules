# Fédération : canal de discussion (`bf_federation_discuss`)

Chaque objet fédéré peut avoir son canal Discuss, où les deux équipes se parlent
dans la forme du clavardage plutôt que dans celle du suivi.

## Le canal est la fenêtre, le chatter est le registre

Un message écrit dans le canal est posté au chatter de l'objet, d'où la
fédération le porte déjà chez le pair. Un message qui arrive au chatter reparaît
dans le canal.

Il n'y a donc **aucun nouveau genre sur le réseau**, aucune conversation en
double, et l'objet garde une seule histoire, celle que le chatter raconte. Le
marqueur d'exclusion 🔒 vaut depuis le canal comme depuis le chatter.

## Qui peut entrer dans un canal fédéré

Entrer dans le canal donne le droit de parler chez le pair, puisque tout ce qui
s'y écrit part au chatter de l'objet puis sur le réseau. Un canal fédéré n'admet
donc que les gens qui pourraient déjà **lire** l'objet lié, et les deux portes
d'entrée sont gardées (par contact et par utilisateur).

⚠️ Le contrôle est à l'entrée, pas à chaque message, et c'est volontaire : au
moment où un message est créé, il n'y a pas d'acteur à juger. Odoo pose les
messages de chatter et de canal en superutilisateur, donc `create_uid` vaut
`__system__`, et `author_id` est souvent le partenaire de la société, qui ne
porte aucun utilisateur.

## Le canal s'ouvre à la demande

*Fédération › Liens*, bouton **Ouvrir le canal**. Jamais tout seul : un canal que
personne n'a ouvert ne sert à personne. Les membres sont les gens d'ici que
l'objet concerne ; le pair n'y est pas, il a le sien chez lui.

## Ce qui ne descend pas au canal

Les notes internes de la fédération. « L'échéance a bougé » est un journal, pas
une conversation, et le canal s'en noierait.

## ⚠️ Ce n'est pas du clavardage en direct

La boîte de sortie est relue par un cron : comptez deux à quatre minutes par
aller-retour. Pour une conversation qui doit être instantanée, le téléphone reste
meilleur.

## Dépendances

`bf_federation`, `mail`.
