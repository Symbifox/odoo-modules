# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont Signature des devis",
    # 18.0.1.0.0: premier jet.
    "version": "18.0.1.0.0",
    "category": "Sales",
    "summary": "« Signé par » d'un devis : le nom légal figé du signataire",
    "description": """
Pont Signature des devis
========================

Un devis signé par ``bf_sign`` reçoit dans « Signé par » le nom des
signataires. C'est la trace de l'acte sur le devis : ce pont, installé de
lui-même, y met le nom légal figé à l'envoi (``bf_preferred_name_sign``), le
nom du signataire à défaut.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name_sign", "bf_sign_sale"],
    "data": [],
    "installable": True,
    "auto_install": True,
    "application": False,
}
