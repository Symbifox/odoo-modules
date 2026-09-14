{
    "name": "Chronomètre : application Android",
    "version": "18.0.1.0.0",
    "category": "Services/Timesheets",
    "summary": "L'API de Symbifox Chronomètre : appariement d'un téléphone et gestes du chrono",
    "description": """
Chronomètre : application Android
=================================

La page « Minuterie » s'installe comme application web, mais sur Android elle
ne tient pas : le routeur d'Odoo ramène toute adresse ``/scoped_app`` vers
``/odoo`` dès que la page ne s'affiche pas en mode autonome, ce qui est le cas
d'un raccourci d'écran d'accueil de Brave et d'un lien ouvert depuis ailleurs.
L'application Symbifox Chronomètre remplace cette page sur le téléphone, et ce
module lui sert d'API.

Ce qu'il ajoute
---------------

* **Un registre d'appareils** (``bf.timer.device``) : code à usage unique de
  cinq minutes, défi PKCE obligatoire, jeton rangé empreinté, dix appareils au
  plus par personne, désactivation après 180 jours sans appel.
* **Les gestes du chrono**, sous ``/bf_timer/mobile/v1`` : l'état en une seule
  lecture, la recherche de tâches, démarrer, mettre en pause, reprendre,
  abandonner, et **enregistrer**.

Ce qu'il ne change pas
----------------------

Il n'écrit rien dans le chronomètre. Chaque geste appelle les méthodes de
``bf.timer`` au nom de la personne, avec ses droits : une feuille de temps
saisie depuis le téléphone est identique à celle saisie depuis le navigateur.

⚠️ **Enregistrer arrête ET saisit dans la même requête.** Le téléphone
n'appelle jamais l'arrêt seul : un chrono arrêté que personne ne confirme reste
en attente, et il faut aller le retrouver.

⚠️ **Exige ``bf_timesheet_timer`` 18.0.1.12.0 ou plus récent** (l'écoulé
figé à l'arrêt). L'installation est refusée sur une version antérieure.

Les appareils se retirent depuis leur vue d'administration ; un pont vers une
page de portail « Mes appareils » existe hors de ce dépôt.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_timesheet_timer"],
    "data": [
        "security/bf_timer_device_security.xml",
        "security/ir.model.access.csv",
        "data/bf_timer_device_cron.xml",
        "views/bf_timer_device_views.xml",
    ],
    # ⚠️ Exige bf_timesheet_timer >= 18.0.1.12.0. `depends`
    # n'exprime aucune version : le crochet refuse l'installation sur un
    # chronomètre qui ne fige pas l'écoulé à l'arrêt.
    "pre_init_hook": "_exiger_chrono_fige",
    "installable": True,
    "application": False,
    "auto_install": False,
}
