# -*- coding: utf-8 -*-
from odoo import fields, models

# Field each user may read/write on their OWN profile (preferences dialog).
DARK_MODE_SELF_FIELDS = ["bf_dark_mode_enabled"]


class ResUsers(models.Model):
    _inherit = "res.users"

    # Per-user dark mode preference. Persisted server-side so the choice
    # follows the user across browsers and devices, and can be set as a
    # default by an administrator. The web client seeds the local cookie and
    # toggle from this value on load (see static/src/js/dark_mode_button.js).
    bf_dark_mode_enabled = fields.Boolean(
        string="Mode sombre (Blue Fox)",
        help="Active le thème sombre Blue Fox pour le client web Odoo. "
             "La préférence vous suit sur tous vos navigateurs et appareils.",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + DARK_MODE_SELF_FIELDS

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + DARK_MODE_SELF_FIELDS
