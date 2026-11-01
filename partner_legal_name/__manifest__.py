{
    "name": "Symbifox — Partner Legal Name",
    "summary": "Separate legal name (DBA) from display name on partners and companies",
    "version": "18.0.1.0.2",
    "category": "Customizations",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    # 18.0.1.0.2: French (fr_CA) translation, tests.
    "depends": ["base", "web"],
    "data": [
        "views/res_partner_views.xml",
        "views/res_company_views.xml",
        "views/external_layout.xml",
    ],
    "installable": True,
    "application": False,
}
