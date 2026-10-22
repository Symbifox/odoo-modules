{
    "name": "Hosting Status Page",
    "summary": "A status page per hosted client, opened by a private link: "
               "services, versions, health and recent updates.",
    # 18.0.1.0.0: première version de la page d'état. Une page par client
    #   commercial, à jeton propre, sans compte : ses services actifs, leur
    #   version, un état lissé au seuil de l'alerte avec l'heure du relevé, la
    #   disponibilité sur 30 jours et les mises à jour appliquées. Ni avis de
    #   sécurité ni fenêtre ponctuelle.
    # 18.0.1.1.0: les fenêtres de maintenance annoncées (hosting.maintenance.window,
    #   hosting_management 18.0.2.63.1) : à venir et des 30 derniers jours, avec les seuls
    #   services du client ; « Maintenance en cours » pendant une fenêtre.
    # 18.0.1.2.0: point d'extension `_hosting_status_completer(valeurs, services)` pour
    #   les sections ajoutées par un autre module ; README corrigé : les services d'un
    #   contact ne paraissent pas sur la page de sa société.
    # 18.0.1.3.0: la page dans le portail (/my/hebergement) pour les destinataires hébergement
    #   du client (hosting_management 18.0.2.64.0) ; l'avis de maintenance porte le lien de la
    #   page quand elle est publiée.
    # 18.0.1.3.1: le lien d'état de l'avis ne part qu'aux destinataires NOMMÉS (pas au repli).
    # 18.0.1.4.0: le jeton masqué dans tout message du chatter (copie de l'avis, réponse
    #   citée) ; chaque lien d'état jugé à l'envoi, par destinataire : le jeton courant à un
    #   destinataire encore nommé d'une page publiée, sinon le paragraphe du lien retiré.
    "version": "18.0.1.4.0",
    "category": "Services",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "installable": True,
    "depends": [
        "hosting_management",
        "portal",
    ],
    "data": [
        "views/res_partner_views.xml",
        "views/status_templates.xml",
    ],
}
