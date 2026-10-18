{
    "name": "BF Calendar Held Slots",
    "summary": "Hold several candidate slots for a meeting that has to move, "
               "see them on the meeting and in their own colour, and move the "
               "meeting to the one that is chosen: the others are released.",
    # 18.0.1.0.0 : plages retenues pour déplacer une rencontre.
    #   Avant ce module, une plage se posait à la main (« RÉSERVÉ pour
    #   déplacer la rencontre ») : aucune reliée à sa
    #   rencontre, et la plage restait par-dessus la rencontre une fois
    #   celle-ci déplacée (cas du 2026-10-10). Une plage retenue est un
    #   événement ordinaire, lié à sa rencontre, au statut Tentative (hachures
    #   de bf_calendar_invite), occupé (la prise de rendez-vous ne l'offre
    #   plus), sans invité ni rappel, et d'une couleur à elle (règle bf_color
    #   livrée, modifiable). « Choisir cette plage » déplace la rencontre
    #   (OdJ, invités et visio suivent ; occurrence de série comprise) et
    #   libère les autres. Arbitrage du 2026-10-11 : choix par
    #   l'organisateur seul, couleur dédiée.
    # 18.0.1.0.1 : la liste des plages de la rencontre suit l'ordre du calendrier et
    #   affiche les heures à la minute.
    # 18.0.1.0.2 : une plage suit la confidentialité de sa rencontre quand elle
    #   change après coup et quand elle y est rattachée par écriture (elle n'était
    #   copiée qu'à la création), jusque dans Nextcloud ; .pot et fr.po livrés
    #   avec le fr_CA.po.
    "version": "18.0.1.0.2",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": [
        "calendar",
        # Le statut Tentative et ses hachures dans la grille.
        "bf_calendar_invite",
        # La couleur libre d'un événement et sa règle automatique.
        "bf_color",
        # Porte bf.color.mixin sur calendar.event et le patch de la grille qui
        # lit `color_resolved` ; pousse aussi les plages vers Nextcloud.
        "calendar_nextcloud_sync",
    ],
    "data": [
        "data/bf_color_rule.xml",
        "data/ir_cron.xml",
        "views/calendar_event_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_calendar_hold/static/src/js/hold_popover.js",
            "bf_calendar_hold/static/src/xml/hold_popover.xml",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
