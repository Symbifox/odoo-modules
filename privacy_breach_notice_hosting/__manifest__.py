{
    "name": "Vie privée : avis de violation depuis l'hébergement",
    "version": "18.0.1.0.0",
    "category": "Privacy/Compliance",
    "summary": "Un événement de sécurité d'hébergement se qualifie (touche-t-il des renseignements "
               "d'un client, et pourquoi pas) et prépare un avis par responsable touché",
    "description": """
Vie privée : avis de violation depuis l'hébergement
===================================================

Installé de lui-même là où l'hébergement et les avis de violation se côtoient.

* **Chaque événement de sécurité se qualifie** : à évaluer, ne touche aucun
  renseignement d'un client, ou en touche. Le motif est exigé dans les deux sens :
  la conclusion « non » se documente aussi.
* **Les organisations touchées** se proposent à partir des services en cause.
* **« Préparer les avis »** crée un brouillon par organisation, prérempli des faits
  de l'événement. Rien ne part sans qu'une personne le signe.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["privacy_breach_notice", "hosting_management"],
    "data": [
        "views/hosting_security_event_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": True,
}
