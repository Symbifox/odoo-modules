# Chronomètre de rencontre

Le temps réel passé sur chaque sujet d'un ordre du jour, et l'heure de fin
projetée pendant que la rencontre dure encore.

## Pourquoi

Un ordre du jour porte déjà ses sujets et leurs minutes allouées. Personne ne
sait pour autant où passent ces minutes : la somme des sujets est bâtie pour
remplir la case du calendrier, et le compte rendu ne dit jamais que le premier
sujet en a mangé la moitié.

## Ce qu'il fait

Un panneau dans l'onglet « Notes en direct » de l'ordre du jour, là où la
personne qui préside a déjà les mains :

* l'heure de fin projetée, contre l'heure de fin prévue ;
* l'écart cumulé contre le plan, sujet par sujet ;
* ce que le sujet qu'on vient de quitter a coûté ;
* ce qu'il reste d'alloué aux sujets pas encore abordés.

Gestes : démarrer, sujet suivant (`Alt+Maj+S`), revenir sur un sujet, passer un
sujet, pause (`Alt+Maj+P`), terminer. Un sujet imprévu né en cours de rencontre est
chronométré comme les autres ; un sujet repris plus tard cumule ses passages.
Revenir aussitôt (moins de 90 secondes) au sujet qu'on vient de quitter annule
le « Sujet suivant » au lieu de compter un passage. Chaque geste qui ouvre un
sujet amène à ce sujet dans les notes en direct. Le panneau se réduit à une
rangée.

« + Varia » ouvre le sujet Varia, créé au premier clic à la fin de l'ordre du
jour, et y pose le curseur.

Les notes en direct se prennent par sujet : les sujets à gauche, les notes du
sujet choisi à droite. Le chronomètre choisit le sujet ; un clic à gauche en
ouvre un autre sans toucher au chronomètre. Ces notes vont aux points du compte
rendu, sujet par sujet, et les « Notes générales » à son résumé. Un réglage de
société rend l'ancien fil continu.

Terminer au chronomètre termine la rencontre, et terminer la rencontre (ou
créer son compte rendu) arrête le chronomètre. La barre d'étapes ramène un
ordre du jour terminé à « Confirmé » ; le chronomètre, lui, ne repart pas.

À la fin, le temps réel de chaque sujet reste sur l'ordre du jour, le chatter
reçoit un récapitulatif, et le rapport de compte rendu imprime le tableau,
sujets jamais abordés compris.

## Ce qu'il n'est pas

Ce n'est pas un chronomètre de speedrun. Les sujets d'une rencontre ne se
répètent pas d'une instance à l'autre : un ordre du jour est réécrit à neuf
chaque fois. Il n'y a donc ni meilleur segment, ni somme des
meilleurs, ni record à battre. La seule comparaison est le plan.

## Dépendances

`bf_meeting`.
