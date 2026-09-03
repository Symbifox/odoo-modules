# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Hébergement — Mises à jour du système",
    "summary": "Relevé des paquets, du noyau et des redémarrages, par système installé",
    "version": "18.0.4.2.0",
    "category": "Services",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "installable": True,
    "application": False,
    # `hosting_management` seul : le module vit sur `hosting.endpoint`, qui y est
    # défini. Rien ici ne dépend du parc client ni d'un connecteur RMM.
    "depends": ["hosting_management", "bf_home"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_hosting_patch_security.xml",
        "data/bf_hosting_patch_cron.xml",
        "views/bf_patch_system_views.xml",
        "views/bf_patch_report_views.xml",
        "views/bf_patch_job_views.xml",
        "views/hosting_endpoint_views.xml",
        "views/hosting_maintenance_schedule_views.xml",
        "views/bf_hosting_patch_menu.xml",
    ],
}
