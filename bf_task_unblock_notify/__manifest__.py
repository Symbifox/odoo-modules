{
    "name": "BF Notification de déblocage de tâche",
    "summary": "Notifie les assignés quand leur tâche est débloquée",
    # 18.0.2.0.0: the notice wears the company's mail layout,
    #   speaks the recipient's time zone, links the blocking tasks, tells
    #   the deadline, and names who removed a dependency. An unassigned task
    #   notifies its project manager. With Gen, the notice can wait for a
    #   game plan written by a locked pass of the bridge (company setting,
    #   off by default).
    # 18.0.1.8.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.2.0.0",
    "category": "Project",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    "depends": ["project", "bf_onboarding_base"],
    "data": [
        "security/ir.model.access.csv",
        "data/unblock_notify_template.xml",
        "data/ir_cron.xml",
        "views/res_config_settings_views.xml",
        "data/bf_onboarding.xml",
    ],
    "installable": True,
    "auto_install": False,
}
