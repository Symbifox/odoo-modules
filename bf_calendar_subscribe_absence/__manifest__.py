{
    "name": "BF Calendar Subscriptions - Out of office",
    "summary": "Repeated events of a subscribed calendar (public holidays) do not "
               "turn on the out-of-office replies.",
    # 18.0.1.0.0 : relecture adverse du lot 6. La détection
    #   d'absence par l'agenda refuse une occurrence de série (un férié répété
    #   n'est pas une absence) ; un agenda abonné arrive déplié, ses occurrences
    #   passaient pour des événements simples et armaient le répondeur. Un
    #   événement isolé d'un agenda abonné (« Congé ») l'arme toujours.
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": ["bf_calendar_subscribe", "bf_email_management"],
    "installable": True,
    "application": False,
    "auto_install": True,
}
