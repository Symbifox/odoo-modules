# -*- coding: utf-8 -*-
{
    "name": "Symbifox Absences des contacts : calendrier Nextcloud",
    # 18.0.1.0.0: premier jet. Lit un calendrier Nextcloud tenu à la main,
    #   celui où l'on note les vacances de ses clients, et en propose les
    #   périodes. Lecture seule : le calendrier reste la feuille de notes de
    #   son propriétaire, et rien n'y est écrit en retour.
    "version": "18.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Proposer les absences notées à la main dans un calendrier "
               "Nextcloud",
    "description": """
Absences des contacts : calendrier Nextcloud
============================================

Avant d'avoir un module, on note les vacances de ses clients quelque part. Souvent, c'est un calendrier Nextcloud tenu à la main. Ce pont le lit.

Ce qu'il comprend
-----------------

Le calendrier réel a deux formes, et le module lit les deux :

* **un marqueur seul** au jour du départ, « Prénom - Société ». La période n'a pas de fin : elle est proposée sans
  date de retour, à compléter ;
* **une paire** qui l'encadre, « Vacances - Prénom Nom » puis « Retour de
  vacances Prénom ». La période va du départ à la veille du retour.

Ce qu'il ne fait pas
--------------------

Il **n'écrit rien** dans le calendrier, et il ne le fait pas entrer dans
l'agenda d'Odoo : le calendrier reste la feuille de notes de son propriétaire.

Il ne crée **aucun contact**. Une entrée dont le nom ne correspond à personne
est rapportée, pas devinée : appeler « David » le mauvais David enverrait le
courrier d'un client à un autre.

Et comme tout ce qui vient d'une machine ici, il **propose** : une personne
accepte ou refuse.

La connexion
------------

Aucun identifiant neuf. Le module réutilise la configuration de synchronisation
de calendrier déjà en place, c'est-à-dire l'adresse du serveur, le compte et le
mot de passe d'application déjà chiffrés dans la base.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    # ⚠️ `bf_contact_absence_mail` porte le modèle de suggestion, qui est la
    # forme commune de « quelque chose propose une absence ». Le pont en dépend
    # donc, ce qui entraîne la boîte unifiée. C'est un couplage assumé et
    # temporaire : la place juste du modèle est le socle, et l'y déplacer est le
    # rangement à faire quand la famille se stabilisera.
    #
    # 🔴 On ne dépend PAS du module de synchronisation de calendrier, bien
    # qu'on réutilise sa connexion. Il exige `googleapiclient`, que toutes les
    # images ne portent pas : en dépendre rendrait ce pont ININSTALLABLE là où
    # la bibliothèque manque, pour une adresse et un mot de passe. La
    # connexion est donc retrouvée à l'exécution, et le module le dit
    # clairement quand elle n'est pas là.
    "depends": [
        "bf_contact_absence",
        "bf_contact_absence_mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/bf_absence_calendar_cron.xml",
        "views/bf_absence_calendar_source_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
}
