{
    "name": "BF Calendar Work Blocks - Online Booking",
    "summary": "Meeting slots booked in your calendar open online booking times, "
               "on top of your working hours.",
    # 18.0.1.0.0 : la plage de rencontre ouvre la prise de rendez-vous (arbitrage du
    #   2026-10-14). Une plage dont le
    #   type « ouvre la prise de rendez-vous » s'ajoute, pour la personne qui la
    #   pose, aux heures de travail ET aux heures du type de rendez-vous : on peut
    #   la réserver même hors de ces heures. Les événements occupés qui la
    #   chevauchent la referment, comme partout. La plage est toujours « libre ».
    #   Cas limites : plages en journée entière lues par leurs dates dans le
    #   fuseau de la personne, ouverture calculée par combinaison (plus de
    #   créneau offert puis refusé), et une journée entière OCCUPÉE ferme toute
    #   la journée locale (elle ne fermait que 8 h à 18 h UTC, 4 h à 14 h à Montréal).
    # 18.0.1.0.1 : une plage de rencontre n'ouvre que l'agenda de qui l'a posée
    #   ou acceptée (un événement au nom d'un collègue ouvrait ses heures).
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    # bf_appointment : sa règle « un événement libre ne ferme jamais un
    # créneau » est ce qui empêche une plage de se refermer elle-même.
    "depends": ["bf_calendar_block", "resource_booking", "bf_appointment"],
    "data": ["views/views.xml"],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": True,
}
