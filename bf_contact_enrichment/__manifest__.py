{
    "name": "Enrichissement de contacts",
    "summary": "Cartes d'affaires (OCR, avec page mobile installable), signatures "
               "courriel, import vCard, détection de doublons et score de "
               "complétude — via la passerelle bf_llm",
    "version": "18.0.1.2.1",
    "category": "Contacts",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": [
        "base",
        "contacts",
        "mail",
        "bf_email_management",
        "bf_llm",
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
