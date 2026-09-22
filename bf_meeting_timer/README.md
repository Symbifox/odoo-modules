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

Gestes : démarrer, sujet suivant (`Alt+S`), revenir sur un sujet, passer un
sujet, pause (`Alt+P`), terminer. Un sujet imprévu né en cours de rencontre est
chronométré comme les autres ; un sujet repris plus tard cumule ses passages.

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
