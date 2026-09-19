# Ressources de tiers embarquées

## Icônes d'émetteur — Simple Icons

`static/src/data/icones.json` embarque les tracés de **371 icônes** issues de
[Simple Icons](https://simple-icons.org) **15.7.0**, projet publié sous
**CC0 1.0**, plus 439 alias qui font correspondre les noms sous lesquels un
service se présente parfois dans une adresse `otpauth://`.

⚠️ **Les icônes qui portent une licence PROPRE dans les données du projet sont
écartées à la fabrication** : le CC0 du projet ne les couvre pas. Dix l'ont été
à ce titre. Relevé le 2026-09-18.

Le fichier est servi **en statique**, hors du paquet d'actifs d'Odoo : il ne se
charge que lorsque le coffre s'ouvre, et le paquet des personnes qui n'ouvrent
jamais le coffre n'en porte rien.

⚠️ **La licence couvre le fichier, pas la marque.** Chaque logo reste la marque
de son propriétaire. L'usage fait ici est nominatif : indiquer de quel service
est un compte que la personne détient déjà.

⛔ **Absents de la source, et c'est voulu par leurs propriétaires** : Microsoft,
LinkedIn, Amazon Web Services, Fastmail. Ils gardent la pastille de couleur. Ne
pas leur dessiner de substitut : une marque approchée est une marque contrefaite.

⚠️ **Cette liste se vérifie contre la source à chaque montée, elle ne se recopie
pas.** Slack et Twilio y ont figuré et sont revenus : le module les embarque
depuis la 18.0.11.0.0. Les annoncer absents pendant que le catalogue les diffuse
serait une contradiction qu'on ne veut pas avoir à expliquer.

### Pourquoi embarquer plutôt que récupérer

Aller chercher la favicon d'un service révélerait à ce service, et à qui
regarde le réseau, la liste des comptes que la personne protège. Les tracés
vivent donc dans le module et aucun octet ne quitte la page.

### Pourquoi celles-là, et comment la liste est faite

⚠️ **La sélection est MÉCANIQUE, et c'est ce qui la rend publiable.** Elle est
l'intersection de deux jeux publics :

1. l'annuaire [2FA Directory](https://2fa.directory), pour les services qui
   gèrent le TOTP ;
2. Simple Icons, pour ceux d'entre eux qui y ont une icône.

Aucun coffre n'a été regardé pour la composer. Le module est publié : une
sélection tirée des émetteurs réels d'une organisation ferait de la liste de ses
services une donnée publique, soit exactement ce que le refus de la favicon
évite. Une règle mécanique sur deux jeux publics ne peut, par construction,
rien dire d'un coffre en particulier.

Le relevé icône par icône, avec le titre et le domaine dont chacune vient, est
dans `static/src/data/icones-provenance.tsv`.

### La correspondance est EXACTE, jamais par préfixe

Deviner « Apple Federal Credit Union » à partir de « Apple » poserait le logo
d'une marque sur le compte d'une autre, ce qui est pire que pas d'icône du tout.
Ce qui ne correspond à rien garde sa pastille, qui est un repli qui ne rate
jamais.
