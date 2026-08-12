import json

from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # JSON list of systray registry keys this user has chosen to hide.
    bf_systray_hidden = fields.Char(
        string="Icônes de la barre système masquées",
        size=4096,
        default="[]",
        help="Liste JSON des clés d'icônes systray masquées pour cet utilisateur.",
    )

    @api.model
    def bf_get_systray_prefs(self):
        """Return the calling user's list of hidden systray keys."""
        raw = self.env.user.sudo().bf_systray_hidden or "[]"
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return [str(k) for k in data] if isinstance(data, list) else []

    @api.model
    def bf_set_systray_prefs(self, keys):
        """Persist the calling user's hidden systray keys (own record only)."""
        if not isinstance(keys, (list, tuple)):
            keys = []
        # Bound both the number of keys and the length of each (registry keys
        # are short) so a client cannot bloat its own user row.
        clean = [str(k)[:64] for k in keys][:50]
        self.env.user.sudo().bf_systray_hidden = json.dumps(clean)
        return True
