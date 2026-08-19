# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Repose la tuile courriel que la mise à jour retire.

Même piège que pour `bf_chatter_chronological` : cette migration tourne AVANT
le nettoyage des orphelins, donc la garde d'idempotence verrait encore
l'ancienne tuile et ne créerait rien, avant que le nettoyage ne l'emporte. On
retire donc l'ancien enregistrement et son `ir.model.data` d'abord.
"""

from odoo.addons.bf_bureau.hooks import _ensure_optional_panes

ANCIEN = "bf_bureau.default_pane_admin_bottom"


def migrate(cr, version):
    if not version:
        return
    from odoo import SUPERUSER_ID, api
    env = api.Environment(cr, SUPERUSER_ID, {})
    rec = env.ref(ANCIEN, raise_if_not_found=False)
    if rec:
        module, nom = ANCIEN.split(".", 1)
        env["ir.model.data"].search([
            ("module", "=", module), ("name", "=", nom),
        ]).unlink()
        rec.unlink()
    _ensure_optional_panes(env)
