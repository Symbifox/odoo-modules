{
    "name": "Gen — consommation Claude dans le digest quotidien",
    "summary": "Sections « Consommation Claude » (fenêtres, bascule, état de la "
               "sonde) et « Conversations Gen à suivre »",
    "version": "18.0.1.3.0",
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
compte et son courriel, la part consommée de chaque fenêtre (session de 5 h,
semaine, semaines par modèle), le temps qui reste avant la bascule en jours et
en heures, et l'heure de la bascule, dite en heure de Montréal.

La section paraît chaque jour tant qu'il y a des comptes : c'est un compteur,
pas une alerte. Un relevé périmé ou en erreur est dit en rouge, parce qu'un
compteur qui ne bouge plus n'est pas un compteur rassurant.

Elle ne paraît qu'aux destinataires administrateurs, les seuls qui voient les
comptes dans Odoo.

Une seconde section, « Conversations Gen à suivre », reprend pour chaque
destinataire ses propres conversations qui attendent quelque chose : celles que
Gen tient pour faites (à archiver), celles qui attendent son geste, et celles
que la passe de nuit a relancées. Elle se tait quand rien n'attend, et tant que
le locataire n'a pas allumé « Les conversations visent leur fermeture ».
""",
    "depends": [
        "bf_claude_chat",
        "daily_todo_digest",
    ],
    "data": [
        "views/daily_digest_views.xml",
    ],
}
