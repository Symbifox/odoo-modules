{
    "name": "Calendar Ergonomics",
    "summary": "Refresh button, reload on return to the tab, keyboard shortcuts, "
               "remembered side panel, Duplicate in the event popover and "
               "working hours shaded in every calendar view.",
    # 18.0.1.0.0 : le calendrier d'Odoo 18 ne se recharge qu'au
    #   changement de date, d'échelle ou de filtre, et aucune synchro
    #   (Nextcloud aux 15 min, Google, Gen) n'avertit l'écran. Ce module
    #   patche la vue calendrier GÉNÉRIQUE (toutes les vues : Agenda, tâches,
    #   rendez-vous...) : ⟳ Actualiser et rechargement au retour sur l'onglet,
    #   raccourcis (Alt+PageUp/PageDown, Alt+↑/↓, Alt+T, Alt+R), panneau
    #   latéral mémorisé (Odoo ne le garde que le temps de l'onglet),
    #   « Dupliquer » dans la bulle (formulaire prérempli, rien n'est créé ni
    #   envoyé avant l'enregistrement) et heures de travail ombrées (horaire de
    #   l'usager, lu dans son fuseau puis converti, jours fériés compris).
    "version": "18.0.1.0.0",
    "category": "Productivity/Calendar",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": [
        "web",
        # Horaires de travail et jours fériés (`_work_intervals_batch`).
        "resource",
    ],
    "assets": {
        "web.assets_backend": [
            "web_calendar_ergonomics/static/src/calendar_ergonomics.js",
            "web_calendar_ergonomics/static/src/calendar_ergonomics.scss",
            # Juste après les gabarits d'Odoo, AVANT ceux de calendar,
            # hr_holidays, etc. : Odoo 18 n'applique une extension à un gabarit
            # dérivé (« primary », comme celui de l'Agenda) que si elle est
            # chargée avant lui. En fin de paquet, le bouton manquait à l'Agenda
            # et ne paraissait que sur les autres calendriers.
            ("after", "web/static/src/views/calendar/calendar_controller.xml",
             "web_calendar_ergonomics/static/src/calendar_controller.xml"),
            ("after", "web/static/src/views/calendar/calendar_common/calendar_common_popover.xml",
             "web_calendar_ergonomics/static/src/calendar_popover.xml"),
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
