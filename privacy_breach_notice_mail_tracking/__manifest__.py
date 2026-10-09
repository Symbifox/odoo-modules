{
    "name": "Vie privée : avis de violation et suivi des courriels",
    "version": "18.0.1.0.0",
    "category": "Privacy/Compliance",
    "summary": "Le suivi des courriels (mail_tracking) ne montre pas les avis de violation à qui n'a "
               "pas le rôle Vie privée",
    "description": """
Vie privée : avis de violation et suivi des courriels
=====================================================

Installé de lui-même là où `mail_tracking` et les avis de violation se côtoient.

`mail_tracking` rend ses suivis et ses événements lisibles par tout utilisateur interne, et le
nom d'un suivi porte l'objet et le destinataire du courriel : le numéro de l'avis, le client,
l'adresse de son responsable. Deux paires de règles réservent ceux des avis au rôle Vie privée,
sans toucher aux autres suivis.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["privacy_breach_notice", "privacy_incident", "mail_tracking"],
    "data": ["security/mail_tracking_security.xml"],
    "installable": True,
    "application": False,
    "auto_install": True,
}
