# -*- coding: utf-8 -*-
{
    "name": "Célébrations",
    "summary": "Tableaux de vœux collectifs, calendrier des occasions, et le "
               "consentement de la personne qu'on souligne",
    "version": "18.0.1.0.2",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste
    # d'Odoo offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est
    # le fichier LICENSE qui gouverne, pas cette ligne, et un recensement
    # fait au manifeste seul rend donc un résultat faux.
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "description": """
Célébrations
============

Une carte de fête signée par tout le bureau, sans que personne n'ait à créer
un compte, et sans qu'Odoo ait à révéler la date de naissance de qui que ce
soit.

Le consentement d'abord, parce que la donnée n'existe pas
---------------------------------------------------------

`hr.employee.birthday` porte l'ANNÉE de naissance, vit derrière
`hr.group_hr_user`, et n'apparaît pas dans `hr.employee.public`, la vue que
les collègues lisent réellement. Et dans la plupart des bases il n'est
tout simplement pas rempli : un module qui le lirait afficherait un
calendrier vide le jour de son installation.

Ce module ne le lit donc jamais. Il demande, une seule fois, et il enregistre
ce que la personne répond : le jour et le mois, sans l'année. Un profil de
célébration appartient à la personne qu'il décrit, et à personne d'autre.

Quatre états, pas une case à cocher. Un booléen ne distingue pas « jamais
répondu » de « a dit non », et c'est exactement cette différence qui décide
si on redemande l'an prochain :

* **En attente** (défaut) : rien ne se passe, une invitation, puis silence.
* **Oui, avec un tableau** : la date paraît au calendrier et on peut lui
  monter une carte collective.
* **Oui, mais sans tableau** : la date paraît, personne n'organise rien.
* **Non merci** : retirée de tout, et plus jamais sollicitée.

Le retrait est silencieux. Aucun décompte de gens masqués, aucune entrée
grise, aucun marqueur sur la fiche employé : sinon le retrait dénoncerait la
personne, et « pourquoi il n'y a pas eu de carte pour X » deviendrait une
petite violence en soi.

Le tableau de vœux
------------------

* Un lien et un code QR que n'importe qui peut suivre, **sans compte**.
* Des messages, des photos et des GIF téléversés (jamais de recherche
  d'images chez un tiers, qui exporterait la requête).
* Une modération facultative, parce qu'un lien public reste un lien public.
* Une livraison programmée : la personne fêtée ne voit rien avant l'heure
  dite, et la retenue est posée dans la règle d'enregistrement, pas dans
  l'interface.
* Un PDF souvenir et un mode diaporama pour l'écran du bureau.

Plus loin que l'anniversaire
----------------------------

L'objet porte un TYPE d'occasion : anniversaire, anniversaire d'embauche et
ses jalons, bienvenue, départ, retraite, félicitations, rétablissement,
condoléances. Le modèle n'est pas à refaire quand l'usage s'élargit.

Le rappel, qui est le vrai produit
----------------------------------

Un calendrier que personne ne regarde ne fait pas de cartes. Dix jours avant,
la personne qui organise reçoit un message avec un bouton qui crée le tableau
et rend le lien. Deux jours avant la livraison, un tableau trop mince déclenche
une relance : trois signatures valent mieux qu'une carte livrée vide.

Ce qu'il ne fait pas
--------------------

* Aucun XP, aucun palmarès de qui signe le plus. Une attention transformée en
  ferme à points cesse d'être une attention.
* Aucune cagnotte : les paiements sont un autre module et une autre
  conversation.
* Aucune vidéo : le filestore, la taille, l'encodage.
""",
    "depends": [
        "hr",
        "mail",
        "portal",
        "calendar",
    ],
    "data": [
        "security/celebrations_security.xml",
        "security/ir.model.access.csv",
        "data/celebration_cron.xml",
        "data/celebration_mail_templates.xml",
        "views/celebration_profile_views.xml",
        "views/celebration_occasion_views.xml",
        "views/celebration_board_views.xml",
        "views/res_config_settings_views.xml",
        "views/celebration_menus.xml",
        "templates/board_public.xml",
        "report/board_report.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_celebrations/static/src/css/celebrations_backend.css",
        ],
    },
}
