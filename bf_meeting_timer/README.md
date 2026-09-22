# Chronomètre de rencontre

Le temps réel passé sur chaque sujet d'un ordre du jour, l'heure de fin
projetée pendant que la rencontre dure encore, et des notes prises sujet par
sujet.

## Pourquoi

Un ordre du jour porte déjà ses sujets et leurs minutes allouées. Personne ne
sait pour autant où passent ces minutes : la somme des sujets est bâtie pour
remplir la case du calendrier, et le compte rendu ne dit jamais que le premier
sujet en a mangé la moitié.

## Pendant la rencontre

« Démarrer la rencontre » lance le chronomètre sur le premier sujet. Le panneau
se pose au-dessus des onglets de l'ordre du jour et reste en haut pendant qu'on
défile dans les notes :

* le temps écoulé, pauses exclues ;
* le sujet en cours, son temps contre son temps prévu, en rouge une fois
  dépassé ;
* l'heure de fin projetée, contre l'heure de fin prévue ;
* l'écart cumulé contre le plan et ce qu'il reste d'alloué ;
* la liste des sujets : prévu, réel, écart, et le nombre de passages.

Sur un écran assez large, la tête du panneau tient sur une rangée ; elle se
réduit, d'un clic, à l'essentiel.

## Les gestes

* **Sujet suivant** (`Alt+Maj+S`) : le sujet en cours est fait, le prochain
  pas encore abordé commence.
* **Revenir** ou **Ouvrir**, sur une ligne de la liste : un sujet repris plus
  tard cumule ses passages. Revenir dans les 90 secondes au sujet qu'on vient de
  quitter annule le « Sujet suivant » au lieu de compter un passage.
* **Varia** : ouvre le sujet Varia, créé au premier clic à la fin de l'ordre du
  jour.
* **Passer** : le sujet est marqué sauté, le temps déjà couru lui reste.
* **Pause** (`Alt+Maj+P`), puis reprendre.
* **Terminer la rencontre** : arrête le chronomètre et passe l'ordre du jour à
  « Terminé ». Dans l'autre sens, terminer l'ordre du jour ou créer son compte
  rendu arrête le chronomètre. La barre d'étapes ramène au besoin l'ordre du
  jour à « Confirmé » ; le chronomètre, lui, ne repart pas.

Les raccourcis ne prennent rien à Odoo : `Alt+S` y sauvegarde la fiche et
`Alt+P` ouvre la fiche précédente.

## Les notes par sujet

L'onglet « Notes en direct » passe en deux colonnes : les sujets à gauche, les
notes du sujet choisi à droite, sous le contexte d'origine. Le chronomètre
choisit le sujet et y pose le curseur ; un clic à gauche en ouvre un autre sans
toucher au chronomètre. Les « Notes générales » reçoivent ce qui ne va à aucun
sujet.

Les notes s'enregistrent avec la fiche, comme tout champ. À la création du
compte rendu, celles de chaque sujet deviennent ses points clés, et les notes
générales son résumé.

## Ce qui reste

Le temps réel de chaque sujet reste sur l'ordre du jour, le fil de discussion
reçoit un récapitulatif, et le rapport de compte rendu imprime le tableau
« Temps par sujet de l'ordre du jour », sujets jamais abordés compris.

## Réglages

Sur la fiche de société, à côté des autres préférences de rencontre :

* **Place du chronomètre de rencontre** : au-dessus des onglets (défaut), ou
  dans l'onglet « Notes en direct ».
* **Notes en direct** : par sujet, en deux colonnes (défaut), ou un fil continu
  qui va au résumé du compte rendu.

## Ce qu'il n'est pas

Ce n'est pas un chronomètre de speedrun. Les sujets d'une rencontre ne se
répètent pas d'une instance à l'autre : un ordre du jour est réécrit à neuf
chaque fois. Il n'y a donc ni meilleur segment, ni somme des
meilleurs, ni record à battre. La seule comparaison est le plan.

## Dépendances

`bf_meeting`.
