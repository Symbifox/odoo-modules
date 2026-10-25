{
    "name": "Contact Enrichment",
    "summary": "Business cards (OCR, with an installable mobile page), email "
               "signatures, LinkedIn profiles, companies completed by an online "
               "search, vCard import, duplicate detection and completeness "
               "score, through Claude",
    # 18.0.2.2.0 : les deux lignées se rejoignent. La ligne Blue Fox
    #   (1.5.x : LinkedIn) et la ligne publiée (2.x : locataire estampé par le
    #   pont, accent de la marque du locataire sur /scan) portent désormais le
    #   même code. Numéro pris au-dessus de tout ce qui est installé (2.1.1
    #   publiée), pour que chaque base joue sa montée.
    # 18.0.2.2.1 : le résumé d'enrichissement posé au fil était une chaîne
    #   ordinaire, donc échappée par message_post — le seul contact jamais
    #   enrichi affichait ses balises en clair.
    # 18.0.2.3.0 : d'un tiers (LinkedIn, RocketReach), rien que des coordonnées
    #   professionnelles, filtrées à la réception ; hôte des URL de vCard lu par
    #   urlparse au lieu d'une sous-chaîne (CodeQL #8).
    # 18.0.2.4.0 : une société créée incomplète part dans une file que Gen
    #   traite par une recherche en ligne (pont /enrich/company-search), puis
    #   propose ou écrit selon la préférence de l'usager.
    # 18.0.2.5.0 : la source passe à l'anglais (le français vit dans
    #   i18n/fr_CA.po : Odoo ne traduit jamais vers en_US) ; la fenêtre de la
    #   proposition de Gen porte son titre, ses sources leur étiquette ; les
    #   textes que la tâche écrit sont dans la langue du demandeur.
    "version": "18.0.2.5.0",
    "category": "Contacts",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": [
        "bf_ai_bridge",
        "base",
        "contacts",
        "mail",
        "bf_email_management",
    ],
    "data": [
        "security/bf_contact_enrichment_security.xml",
        "security/ir.model.access.csv",
        "data/bf_contact_enrichment_cron.xml",
        "wizard/bf_contact_wizard_views.xml",
        "views/server_actions.xml",
        "views/res_partner_views.xml",
        "views/bf_email_views.xml",
        "views/menus.xml",
        "views/portal_card_templates.xml",
    ],
    "installable": True,
    "application": False,
}
