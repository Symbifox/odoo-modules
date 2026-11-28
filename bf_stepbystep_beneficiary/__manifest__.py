{
    "name": "Step-by-Step: beneficiary on the client page",
    "summary": "The progress page names the beneficiary of the mandate, and "
               "speaks its language, when someone else orders and pays",
    # Un pont, et non une dépendance de bf_stepbystep_clients vers
    # bf_project_beneficiary. Le portail est posé sur des bases qui n'ont
    # aucun bénéficiaire, et une dépendance franche y aurait emporté le champ
    # ET le renommage du client standard en « donneur d'ordre » sur tous
    # leurs projets.
    #
    # 🔴 `auto_install` ne pose le pont QUE dans la passe où l'une des deux
    # dépendances est elle-même en train de s'installer. `button_install()`
    # (odoo/addons/base/models/ir_module.py) exige `'to install' in states` :
    # deux dépendances DÉJÀ installées ne le déclenchent pas, et un `-u` ne
    # passe jamais par cette branche. Le pont reste alors `uninstalled`, SANS
    # une ligne d'erreur. Une pose doit donc le nommer en `-i`, toujours ;
    # `auto_install` ne sert qu'au locataire qui installera plus tard l'un des
    # deux, et à ne RIEN poser là où un seul des deux existe.
    "version": "18.0.1.0.1",
    "category": "Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "depends": [
        "bf_stepbystep_clients",
        "bf_project_beneficiary",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
