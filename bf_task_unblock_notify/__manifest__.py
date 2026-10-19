{
    "name": "BF Notification de déblocage de tâche",
    "summary": "Notifie les assignés quand leur tâche est débloquée",
    # 18.0.2.0.3: the notice wears `bf_onboarding_base.bf_mail_layout`, the
    #   layout every Blue Fox module points its emails to (`bluefox_branding`
    #   replaces it when installed), and its body reads the accent from
    #   `report_brand_primary`, like the layout.
    # 18.0.2.0.2: the body of the notice takes the company's accent and the
    #   brand greys, as the other branded modules do; the frame of the game
    #   plan is a table cell (Odoo's sanitizer drops `border-left`).
    # 18.0.2.0.1: game plan cleaning, two dots in a row no longer cost the
    #   line, phone numbers with wider separators go.
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
    "version": "18.0.2.0.3",
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
