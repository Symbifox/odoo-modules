{
    "name": "Avis de violation : pas de SMS",
    "version": "18.0.1.0.0",
    "category": "Privacy/Compliance",
    "summary": "Un avis de violation ne part pas par SMS : le courriel fait foi",
    "description": """
Avis de violation : pas de SMS
==============================

Installé de lui-même là où l'avis de violation et les SMS se côtoient.

Le « renvoi » d'un SMS (`sms.resend`) crée en sudo un SMS rattaché à un message existant, vers un
numéro fourni par l'appelant : un lecteur faisait ainsi partir le texte du fil de l'avis depuis
l'expéditeur SMS de la société. L'avis part par courriel, à l'adresse désignée, et c'est ce
courriel qui fait foi : aucun SMS ne se rattache à un message de son fil.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["privacy_breach_notice", "sms"],
    "data": [],
    "installable": True,
    "application": False,
    "auto_install": True,
}
