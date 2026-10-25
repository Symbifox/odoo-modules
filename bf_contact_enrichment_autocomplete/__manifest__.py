{
    "name": "Contact Enrichment: Odoo Autocomplete",
    "summary": "Without autocomplete credit, the chosen company is completed "
               "by Gen instead of an invitation to buy credit",
    # 18.0.1.0.0 : pont entre bf_contact_enrichment et partner_autocomplete
    #. Installé à la main là où les deux sont présents : un module
    #   auto_install s'installerait au milieu de la montée d'un autre, et
    #   poser_apres_arret.sh le refuse pour cette raison.
    # 18.0.1.1.0 : la source passe à l'anglais.
    "version": "18.0.1.1.0",
    "category": "Contacts",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_contact_enrichment", "partner_autocomplete"],
    "data": [],
    "installable": True,
    "auto_install": False,
    "application": False,
}
