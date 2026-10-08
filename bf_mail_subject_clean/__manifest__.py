{
    "name": "BF Nettoyage des sujets de courriel",
    "summary": "Évite l'empilement « Re: Re: Re: » dans les sujets envoyés via le chatter",
    # 18.0.1.2.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.2.0",
    "category": "Productivity/Email",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    "depends": ["mail", "bf_onboarding_base"],
    "data": [
        "data/bf_onboarding.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
