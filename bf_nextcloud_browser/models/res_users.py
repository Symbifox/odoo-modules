"""Archiving an Odoo account, or changing its login, ends its Nextcloud connections.

A Nextcloud app password is a permanent token: nothing on the Nextcloud side
ends it when someone leaves. Archiving the Odoo account is the gesture that
already marks a departure here, so it deletes the connections Odoo holds, and
deleting a connection revokes its app password on Nextcloud (best effort).

A login change does the same. The connection was accepted because the Nextcloud
account matched the login: an account handed over to someone else under a new
login must not keep the previous holder's files.
"""

from odoo import models


class ResUsers(models.Model):
    _inherit = "res.users"

    def write(self, vals):
        ending = self.browse()
        if "login" in vals:
            new_login = (vals["login"] or "").strip().lower()
            ending = self.sudo().filtered(lambda u: (u.login or "").strip().lower() != new_login)
        res = super().write(vals)
        if "active" in vals and not vals["active"]:
            ending |= self
        if ending:
            self.env["bf.nc.user.credential"].sudo().search(
                [("user_id", "in", ending.ids)]
            ).unlink()
        return res
