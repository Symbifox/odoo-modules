# Fédération : échéanciers (`bf_federation_gantt`)

Deux firmes sur un chantier commun partagent un échéancier : l'entrepreneur
général et son sous-traitant, l'agence et son client, le consultant et l'équipe
qui implante. Envoyé en PDF, il est périmé le lendemain, et chacun recopie les
dates dans son propre outil.

## Ce qui traverse

L'échéancier autonome de `bf_gantt` : son nom, son statut, ses dates, ses notes
en texte, et toutes ses lignes avec leur couloir, leurs dates, leur jalon, leur
avancement, leur responsable, leur statut et leurs dépendances. Le miroir est un
**vrai** échéancier chez le pair, qui se lit dans le composant Gantt.

## Ce qui ne traverse pas

Les **heures prévues** de chaque ligne. Un échéancier partagé porte des dates,
pas l'effort que chacun y met.

## Ce qui bouge suit

Déplacer une ligne, marquer un jalon atteint, changer l'avancement ou le statut
du plan renvoie l'échéancier. Une empreinte évite de renvoyer ce qui n'a pas
changé, et **Renvoyer au pair** l'envoie tel qu'il est, à la demande.

## Le miroir se lit

Un échéancier reçu ne se retouche pas, ni ses lignes : le prochain changement de
l'émetteur le remplacerait. Il ne se **publie pas au portail** du pair non plus :
l'émetteur l'a partagé avec une entreprise, pas avec les clients de celle-ci.

## Une carte hostile ne bloque rien

Une ligne sans date valide est écartée, un avancement hors bornes est ramené
entre 0 et 100, et une dépendance qui fermerait un cycle est rompue. Sans cela,
la contrainte de `bf_gantt` refuserait la réception entière, pour toujours.

## Qui peut le recevoir

Le **client** du plan cadre les pairs proposés ; à défaut, les pairs nommés sur
le projet cité. Jamais « tous les pairs actifs ».

## Dépendances

`bf_federation`, `bf_gantt`.
