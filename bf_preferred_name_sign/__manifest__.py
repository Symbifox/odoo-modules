# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont Signature",
    # 18.0.1.0.0: premier jet. Un acte signé porte le
    #   nom légal du signataire ; les courriels et le portail gardent son nom
    #   d'usage.
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Le nom légal du signataire sur l'acte, son nom d'usage partout ailleurs",
    "description": """
Pont Signature
==============

Un signataire de ``bf_sign`` porte un seul nom, figé à l'envoi, qui servait à
la fois à l'acte (certificat d'achèvement, pavé « nom » estampé sur le PDF,
page publique de vérification) et à la courtoisie (« Bonjour … » des
courriels, portail). Ce pont, installé de lui-même quand les deux modules sont
là, sépare les deux :

* **Le nom de l'acte** est figé à l'envoi, à côté du nom du signataire : le nom
  légal de sa fiche (``bf_preferred_name``), seulement s'il en a un. Le
  certificat, le pavé « nom » et la page de vérification le lisent.
* **Le nom d'usage** reste celui des courriels, du portail et du journal de
  signature.

Le nom de l'acte n'est lisible que du groupe « Noms légaux ». Aucun chemin
ne l'écrit sur la fiche : la recopie du nom du signataire vers un contact
créé à l'envoi reste celle du nom d'usage.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name", "bf_sign"],
    "data": [
        "views/bf_sign_templates.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
