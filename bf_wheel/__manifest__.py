# -*- coding: utf-8 -*-
{
    "name": "Roue de fortune",
    "version": "18.0.1.2.2",
    "category": "Productivity",
    "summary": "Une roue projetée qui tire au hasard, et le registre de ses tirages",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "application": True,
    "installable": True,
    "auto_install": False,
    "depends": ["base", "web", "bf_live_base"],
    "data": [
        "security/bf_wheel_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/bf_wheel_views.xml",
        "views/bf_wheel_spin_views.xml",
        "views/bf_wheel_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_wheel/static/src/js/**/*.js",
            "bf_wheel/static/src/xml/**/*.xml",
            "bf_wheel/static/src/scss/bf_wheel.scss",
        ],
        "web.assets_tests": [
            "bf_wheel/static/tests/tours/**/*.js",
        ],
    },
    "description": """
Roue de fortune
===============

Une roue qu'on projette devant une salle, qu'on fait tourner, et qui s'arrête sur
un nom. Elle sert au tirage de présence d'un atelier, au choix du prochain sujet,
au gage d'une formation.

Le gagnant est tiré par le serveur, pas par l'animation
-------------------------------------------------------
L'animation ne choisit rien : elle rejoint un résultat déjà pris. Le serveur tire,
écrit le tirage à son registre, puis renvoie à l'écran le rang gagnant **et la liste
exacte sur laquelle il a tiré**. L'écran redessine cette liste avant de tourner, pour
que la roue qu'on regarde soit celle d'où sort le résultat.

C'est ce qui sépare un tirage d'une animation : une roue qui choisit dans le
navigateur ne se vérifie pas, se relance jusqu'au résultat voulu, et ne peut porter
aucun prix.

Le poids d'un segment
---------------------
Chaque segment porte un poids entier, au moins 1. Un poids de 3 occupe trois fois
plus de place sur la roue qu'un poids de 1, et sort trois fois plus souvent : la part
dessinée et la chance tirée sont le même nombre.

Le registre
-----------
Chaque tour écrit une ligne : la date, qui a fait tourner, le gagnant, le nombre de
candidats, le poids total, et la liste des candidats telle qu'elle était. Une ligne du
registre ne se récrit pas et ne s'efface pas à la main : seules les coordonnées de la
personne tirée s'y ajoutent, et elles seules en repartent.

⚠️ Effacer la roue emporte son registre : la cascade se fait dans la base, sous la
garde du modèle, qui ne la voit pas passer.

Les coordonnées de la personne tirée
------------------------------------
Un tirage qui donne un prix doit pouvoir rejoindre qui l'a gagné. La roue demande donc,
en option, le nom et une coordonnée de la personne tirée, au moment du tirage, à elle
seule, et non à toute la salle en entrant.

Ce sont des renseignements personnels : la roue porte un délai de conservation (90 jours
par défaut). Passé le délai, une tâche planifiée efface le nom, la coordonnée et la liste
des candidats, et marque la ligne comme élaguée. Le gagnant et le compte restent : c'est
la preuve du tirage.
""",
}
