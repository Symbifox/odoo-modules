from odoo import SUPERUSER_ID, http
from odoo.http import request

from .. import impersonation as imp


class BfImpersonateController(http.Controller):

    @http.route("/bf_impersonate/stop", type="json", auth="user", methods=["POST"])
    def stop(self):
        """Revenir à son compte. Sans incarnation en cours (durée passée, déjà
        rendue à la requête précédente), rien à faire : le client recharge."""
        payload = imp.current()
        if not payload:
            return {"stopped": False}
        imp.restore(payload, "manual")
        request.env(user=SUPERUSER_ID)["bf.impersonate.session"].browse(
            payload["journal_id"])._bf_close("manual")
        return {"stopped": True}
