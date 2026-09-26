{
    "name": "Gen — consommation Claude dans le digest quotidien",
    "summary": "Section « Consommation Claude » : fenêtres, bascule et état de "
               "la sonde, compte par compte",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # Satellite à part, comme `bf_hosting_patch_digest` : le relevé ne doit pas
    # imposer le digest, ni l'inverse. Pas d'auto-installation : les comptes
    # n'existent que là où la sonde verse, et ce n'est pas partout.
    "auto_install": False,
    "description": """
Pont Gen ↔ Digest quotidien
===========================

Ajoute au digest quotidien le relevé que la sonde de consommation verse
régulièrement dans `claude.account` : pour chaque compte, l'état jugé aux seuils du
compte, la part consommée de chaque fenêtre (session de 5 h, semaine, semaines
par modèle) et l'heure de la bascule, dite en heure de Montréal.

La section paraît chaque jour tant qu'il y a des comptes : c'est un compteur,
pas une alerte. Un relevé périmé ou en erreur est dit en rouge, parce qu'un
compteur qui ne bouge plus n'est pas un compteur rassurant.

Elle ne paraît qu'aux destinataires administrateurs, les seuls qui voient les
comptes dans Odoo.
""",
    "depends": [
        "bf_claude_chat",
        "daily_todo_digest",
    ],
    "data": [
        "views/daily_digest_views.xml",
    ],
}
