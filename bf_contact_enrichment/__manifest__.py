{
    "name": "Enrichissement de contacts",
    "summary": "Cartes d'affaires (OCR, avec page mobile installable), signatures "
               "courriel, import vCard, détection de doublons et score de "
               "complétude — via le pont bf_ai_bridge",
    # 2.0.0 : la lecture ne passe plus par bf_llm (API HTTP + clé, qu'aucun
    # locataire n'a) mais par le pont, donc par l'abonnement Claude du
    # locataire. Changement de dépendance, donc changement majeur.
    "version": "18.0.2.0.0",
    "category": "Contacts",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": [
        "base",
        "contacts",
        "mail",
        "bf_email_management",
        "bf_ai_bridge",
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
