from datetime import timedelta

from odoo import fields, http
from odoo.http import request

from ..models.tools import acting_in_own_web_session
from ..models.work_signal import PRESENCE_MAX_INACTIVITY_MS


class WorkHoursController(http.Controller):

    @http.route("/bf_work_hours/heartbeat", type="json", auth="user", methods=["POST"])
    def heartbeat(self, inactivity_ms=None):
        """The browser says the person used the keyboard or the mouse recently.

        Only for the person of THIS web session (never another account, never
        an API key, never under an impersonation), and only if they turned
        "Propose my hours" on. Returns whether to keep reporting.
        """
        env = request.env
        if not acting_in_own_web_session(env):
            return {"enabled": False}
        if env.uid not in env["bf.work.consent"].sudo()._bf_active_user_ids("hours"):
            return {"enabled": False}
        try:
            inactivity = int(inactivity_ms)
        except (TypeError, ValueError):
            return {"enabled": True}
        if 0 <= inactivity <= PRESENCE_MAX_INACTIVITY_MS:
            moment = fields.Datetime.now() - timedelta(milliseconds=inactivity)
            env["bf.work.signal"].sudo()._bf_record_presence(env.uid, moment)
        return {"enabled": True}
