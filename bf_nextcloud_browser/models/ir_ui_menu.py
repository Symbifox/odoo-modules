"""Hide the Nextcloud app from people who could only open it onto an error.

The top-level menu was gated by the browser group alone. On an instance where
the module is installed and the group is populated but no usable storage
configuration exists, every member saw a "Nextcloud" app whose every click
answered "Aucune configuration Nextcloud disponible".

Administrators included: on the instances where this was found, every member
of the group was an administrator, so an exception for them would have left
the defect exactly where it was. They lose nothing by it: the configuration is
created from Knowledge > Configuration > Nextcloud Documents
(bf_document_nextcloud_sync), and the app reappears as soon as a usable
configuration exists. `load_menus` is cached per user, which is why the
configuration model clears the cache when a configuration appears, disappears
or changes prefix.
"""

from odoo import models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    def get_user_roots(self):
        roots = super().get_user_roots()
        menu = self.env.ref(
            "bf_nextcloud_browser.menu_nc_browser_root", raise_if_not_found=False
        )
        if menu and menu in roots and not self.env["bf.nc.browser"]._browsable_config():
            roots -= menu
        return roots
