{
    "name": "Contacts Nextcloud Sync",
    "summary": "Synchronize Odoo contacts with Nextcloud address book via CardDAV",
    # 18.0.1.3.0: les messages de l'essai de connexion, des résumés d'envoi et
    #   de lecture, et le titre des notifications étaient des f-strings : ils se
    #   lisaient en anglais quelle que soit la langue de l'usager. Ils passent
    #   par _().
    # 18.0.1.3.0: le module était déjà écrit en anglais, mais n'avait aucun
    #   catalogue français : un usager réglé en fr_CA le lisait en anglais.
    #   i18n/fr_CA.po et le .pot du module portent maintenant le français.
    "version": "18.0.1.3.0",
    "category": "Contacts",
    "author": "Les services de consultation Blue Fox, Inc.",
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    "application": False,
    "installable": True,
    "depends": ["contacts", "base_setup"],
    "data": [
        "security/ir.model.access.csv",
        "views/nextcloud_contacts_sync_config_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
        "views/menu.xml",
        "data/nextcloud_contacts_sync_cron.xml",
    ],
}
