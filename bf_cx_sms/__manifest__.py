{
    "name": "Expérience client : invitation de sondage par SMS",
    "summary": "Lien de sondage envoyé par SMS aux contacts sans courriel",
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
Pont Expérience client ↔ SMS
============================

S'auto-installe quand bf_cx et bf_sms_archive sont tous deux installés.
Ajoute sur la vague d'envoi un bouton « Inviter par SMS » qui rejoint les
destinataires SANS adresse courriel mais avec un numéro de téléphone :
chaque contact reçoit par SMS son lien de sondage personnel (jeton
individuel), via la ligne SMS configurée dans les paramètres. Opt-in
(défaut désactivé), action manuelle uniquement (aucun cron), limitée à
5 SMS par clic pour respecter le plafond quotidien du fournisseur SMS,
garde-fous anti-sursollicitation de bf_cx appliqués.
""",
    "depends": [
        "bf_cx",
        "bf_sms_archive",
    ],
    "data": [
        "views/bf_cx_wave_views.xml",
        "views/res_config_settings_views.xml",
    ],
}
