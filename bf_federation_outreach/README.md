# Fédération : suivis de démarchage (`bf_federation_outreach`)

Une agence démarche pour le compte de son client, et les deux sont sur Symbifox.
Le client veut savoir qui a été joint, qui a répondu, où en est chaque cible, et
surtout pouvoir dire « pas celle-là, c'est déjà notre client » avant l'appel.

## Un suivi, pas une campagne

Ce qui traverse est un **suivi** de la campagne, en lecture. Chez le client, le
miroir n'est jamais une campagne de démarchage : une campagne porte une cadence
et une tâche planifiée qui crée des activités, et un miroir ne doit rien
déclencher chez celui qui le reçoit. Le client **n'a pas besoin** de
l'application Démarchage pour lire un suivi.

## Aucune donnée personnelle par défaut

Par défaut, seules l'entreprise ciblée, son étape, ses touches datées et leur
issue traversent. Le nom de la personne-ressource, sa fonction, ses coordonnées
et le résumé des touches ne traversent que si l'agence coche **Inclure les
personnes-ressources**, pour un mandat où le client est responsable de ces
renseignements. Le détail d'une touche et le motif d'un « ne pas contacter » ne
traversent jamais.

Un pair qui glisserait des coordonnées sans annoncer la case ne les voit pas
posées : le receveur ne garde que ce que la carte annonce.

## Écarter une cible

Chez le client, **Écarter** une cible la fait passer à « ne pas contacter » chez
l'agence, avec le motif donné. C'est la seule chose qui revient, et elle ne sait
qu'écarter : jamais ajouter une cible, jamais changer une étape.

La référence de la cible arrive du réseau : elle ne désigne jamais qu'une ligne du
suivi rattaché au lien, et c'est cette ligne qui mène à sa cible dans sa
campagne. Une référence qui ne désigne aucune ligne du suivi ne touche rien.

## La suite

Le suivi se relit et repart **chaque jour**, et à la demande. Un écart décidé par
le client survit aux rafraîchissements des deux côtés. Un suivi dont la campagne
a disparu ne prive pas les autres de leur rafraîchissement.

## Dépendances

`bf_federation`. L'agence a besoin de `bf_outreach` pour tirer un suivi d'une
campagne ; le client, non.
