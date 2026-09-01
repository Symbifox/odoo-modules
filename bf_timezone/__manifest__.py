# Copyright 2026 Les Services de consultation Blue Fox Inc.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    "name": "Symbifox Timezone Utilities",
    # 18.0.1.1.0: la table Windows->IANA couvre l'Asie-Pacifique. Un TZID
    #   « New Zealand Standard Time » ne s'y résolvait pas et une invitation
    #   arrivait décalée de douze heures. Tâche BF #25173.
    "version": "18.0.1.1.0",
    "summary": "Shared timezone helpers and a configurable default timezone "
               "for Blue Fox modules",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "category": "Technical",
    "depends": ["base_setup"],
    "data": [
        "data/ir_config_parameter.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
}
