{
    "name": "BF Calendar Sharing",
    "summary": "Choose what each colleague sees of your calendar: availability "
               "only, titles and locations, all details, or the right to edit.",
    # 18.0.1.0.0 : partage interne par niveau (lot 4).
    #   Le cœur d'Odoo 18 ne connaît que deux niveaux, posés par l'auteur de
    #   chaque événement : tout (sauf le privé) ou « Occupé ». Arbitrage
    #   du 2026-10-14 : quatre niveaux, ceux d'Outlook moins le
    #   délégué (disponibilités, titres et lieux, tous les détails, modifier).
    #   Réglé par agenda et par collègue, avec un niveau par défaut dans les
    #   préférences. Un invité voit toujours la rencontre ; un événement privé
    #   reste « Occupé » à tous les niveaux. Rien ne change à l'installation :
    #   le défaut est « tous les détails », le comportement du cœur.
    # 18.0.1.1.0 : liens vers l'extérieur (lot 5). Abonnement iCalendar, disponibilités
    #   (VFREEBUSY) et page web, à jeton, filtrés côté serveur selon le niveau
    #   (disponibilités, titres et lieux, tous les détails). Arbitrage du
    #   2026-10-14 : les événements « internes seulement » y paraissent au niveau
    #   choisi, le privé reste « Occupé » ; portée choisie par lien (pont Nextcloud) ;
    #   expiration à 90 jours, rappel sept jours avant ; journal des lectures gardé
    #   un an (réglable). Jamais de participants, de lien de visio ni d'étiquettes.
    #   « Lien bidirectionnel Outlook » : Outlook s'abonne à ce lien ; dans l'autre
    #   sens, voir bf_calendar_subscribe (lot 6). Aucun protocole fermé.
    # 18.0.1.1.1 : paramètre système `bf_calendar_share.base_url` pour l'adresse publique
    #   des liens (www. redirigeait en 308 chez BF).
    "version": "18.0.1.1.4",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": ["calendar", "mail"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/bf_calendar_share_views.xml",
        "views/bf_calendar_share_link_views.xml",
        "views/share_page_templates.xml",
        "data/ir_cron.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
