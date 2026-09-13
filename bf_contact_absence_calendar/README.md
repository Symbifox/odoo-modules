# Absences des contacts : calendrier Nextcloud (`bf_contact_absence_calendar`)

Avant d'avoir un module, on note les vacances de ses clients quelque part. Souvent, c'est un calendrier Nextcloud tenu à la main. Ce pont le lit.

## Les deux formes qu'il comprend

Le calendrier réel en porte deux, et les deux comptent :

- **un marqueur seul** au jour du départ, « Prénom - Société »,
  « Prénom (Société) - vacances ». La période n'a pas de fin : elle est proposée
  sans date de retour, à compléter d'un geste ;
- **une paire** qui l'encadre, « Vacances - Prénom Nom » puis
  « Retour de vacances Prénom ». La période va du départ à la veille du
  retour.

🔴 **Le retour ne répète presque jamais le nom de famille.** Apparier les deux
entrées à l'identique laissait la période sans fin. Le rapprochement se fait
donc sur un préfixe, avec le retour le plus proche après le départ.

🔴 **Le mot qui dit la nature peut être en suffixe** : « Société - vacances »
autant que « Vacances - Société ». Pris pour un indice de société, il empêchait
tout appariement.

## Ce qu'il ne fait pas

Il **n'écrit rien** dans le calendrier et ne le fait pas entrer dans l'agenda
d'Odoo : le calendrier reste la feuille de notes de son propriétaire.

Il ne crée **aucun contact**, et refuse une correspondance ambiguë. « David »
tout seul peut désigner trois personnes, et se tromper enverrait le courrier
d'un client à un autre. Une entrée qu'il ne reconnaît pas est **rapportée**,
avec son titre, pas devinée.

Et comme tout ce qui vient d'une machine ici, il **propose** : une personne
accepte ou refuse.

## La connexion

Aucun identifiant neuf : le module réutilise la configuration de
synchronisation de calendrier déjà en place, c'est-à-dire l'adresse du serveur,
le compte et le mot de passe d'application déjà chiffrés dans la base.

⚠️ **Il n'en dépend pas pour autant.** Le module qui porte cette configuration
réclame le client Google, que toutes les images ne portent pas : en dépendre
rendrait ce pont ININSTALLABLE là où la bibliothèque manque, pour une adresse et
un mot de passe. La configuration est donc retrouvée dans le registre à
l'exécution, et le module le dit clairement quand elle n'est pas là.
