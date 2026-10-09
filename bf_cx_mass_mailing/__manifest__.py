{
    "name": "Expérience client : exclusion des boucles ouvertes (mailing)",
    "summary": "Option pour exclure des envois de masse les contacts en boucle CX ouverte",
    # 18.0.1.1.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.1.0",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Courriel de masse
==========================================

S'auto-installe quand bf_cx et mass_mailing sont tous deux installés.
Ajoute aux envois de masse une case « Exclure les boucles CX ouvertes »
(décochée par défaut) : à l'envoi, les destinataires dont le contact a
un feedback à rappeler non traité ou une plainte ouverte sont retirés
de la liste (appariement par courriel normalisé), pour ne pas envoyer
de promotion à quelqu'un qu'on est en train de rattraper. Fonctionne
pour les listes de diffusion (mailing.contact) comme pour les contacts
(res.partner). Aucun envoi nouveau ; sans l'option, le comportement
standard est strictement inchangé.
""",
    "depends": [
        "bf_cx",
        "mass_mailing",
    ],
    "data": [
        "views/mailing_mailing_views.xml",
    ],
}
