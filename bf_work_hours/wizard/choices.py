from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.misc import format_date

from ..models.tools import acting_in_own_web_session, impersonating


class WorkChoices(models.TransientModel):
    """The person's own two switches, reachable from the app.

    A plain page, readable by every internal user, that only ever shows and
    changes the reader's own choices. On some databases the profile page fails
    for people without administration rights; this does not depend on it.
    """

    _name = "bf.work.choices"
    _description = "Working time: my choices"
    # bf_impersonate hides this from whoever impersonates the person.
    _gen_scope = "bf_work_hours"

    # 🔴 Computed at every read, never stored: a button saves the page before it
    # runs, and the page then reloads that saved record. A value stored at
    # creation showed "off" to a person who had just turned it on, and hid the
    # button to turn it off (seen in the browser).
    hours_enabled = fields.Boolean(string="Propose my hours", compute="_compute_enabled")
    insights_enabled = fields.Boolean(string="My indicators", compute="_compute_enabled")
    history_enabled = fields.Boolean(string="My earlier days", compute="_compute_enabled")
    history_status = fields.Char(string="Taken in", compute="_compute_enabled")

    @api.depends_context("uid")
    def _compute_enabled(self):
        page = self._bf_state()
        for choices in self:
            for name, value in page.items():
                choices[name] = value

    @api.model
    def _bf_state(self):
        """The reader's own switches, and how far their earlier days are taken in."""
        history = self._bf_enabled("history")
        return {"hours_enabled": self._bf_enabled("hours"), "insights_enabled": self._bf_enabled("insights"),
                "history_enabled": history, "history_status": self._bf_history_status() if history else False}

    @api.model
    def _bf_history_status(self):
        Consent, Day = self.env["bf.work.consent"].sudo(), self.env["bf.work.day"]
        history = Consent._bf_history(self.env["res.users"].browse(self.env.uid))
        bounds = Day._bf_history_bounds(history)
        if not bounds:
            return False
        first, reached = bounds
        if reached <= first:
            return _("From %(date)s", date=format_date(self.env, first))
        if not history.computed_from:
            return _("Starting, back to %(target)s", target=format_date(self.env, first))
        return _("In progress: back to %(date)s so far, %(target)s at the end",
                 date=format_date(self.env, reached), target=format_date(self.env, first))

    def _compute_display_name(self):
        for choices in self:
            choices.display_name = _("My choices")

    @api.model
    def default_get(self, fields_list):
        # 🔴 The first onchange of a new record puts False in every field without
        # a default, computed or not: the switches showed "off" and the title
        # "New". What the page shows when it opens comes from here.
        res = super().default_get(fields_list)
        page = dict(self._bf_state(), display_name=_("My choices"))
        res.update({name: value for name, value in page.items() if name in fields_list})
        return res

    @api.model
    def _bf_enabled(self, scope):
        return self.env.uid in self.env["bf.work.consent"].sudo()._bf_active_user_ids(scope)

    @api.model
    def _bf_page(self):
        """The page again, fresh, by a full reload of the web client.

        🔴 Not a plain window action: the browser activity service stops asking
        once the server says "off", and only a reload starts it again. A page
        opened without reloading left an activation without browser activity
        until the next reload (seen in 18.0.1.0.1 before release). The reload
        also lands on a new page, read again, with no record number in the
        address (these pages are vacuumed, so a bookmark would outlive it).
        """
        action = self.env.ref("bf_work_hours.action_bf_work_choices")
        return {"type": "ir.actions.client", "tag": "reload", "params": {"action_id": action.id}}

    def _act(self, method, scope):
        # 🔴 Not ``self.env.user``: in Odoo 18 it is a SUPERUSER record, and the
        # guards of res.users let superuser code through. The reader, as themselves.
        me = self.env["res.users"].browse(self.env.uid).with_context(bf_work_scope=scope)
        result = getattr(me, method)()
        if result.get("tag") == "reload":
            return self._bf_page()
        return result

    def action_activate_hours(self):
        return self._act("action_bf_work_activate", "hours")

    def action_activate_insights(self):
        return self._act("action_bf_work_activate", "insights")

    def action_withdraw_hours(self):
        return self._act("action_bf_work_withdraw", "hours")

    def action_withdraw_insights(self):
        return self._act("action_bf_work_withdraw", "insights")

    def action_activate_history(self):
        return self._act("action_bf_work_activate", "history")

    def action_withdraw_history(self):
        return self._act("action_bf_work_withdraw", "history")

    # ------------------------------------------------------------------
    # The first visit
    # ------------------------------------------------------------------

    @api.model
    def _bf_home(self):
        """The app's entry: the consent screen the first time, the person's days after.

        "The first time" = the person never turned anything on and never chose to
        go on without it. Never to someone impersonating them: they could not
        answer for the person, and must not learn whether the person decided.
        """
        if self._bf_needs_welcome():
            return {"type": "ir.actions.client", "tag": "bf_work_hours.consent", "name": _("Working time"),
                    "target": "current", "params": {"scope": "hours", "mode": "welcome"}}
        return self.env["ir.actions.act_window"]._for_xml_id("bf_work_hours.action_bf_work_day_mine")

    @api.model
    def _bf_needs_welcome(self):
        if self.env.su or impersonating() or not acting_in_own_web_session(self.env):
            return False
        uid = self.env.uid
        return not (self.env["bf.work.consent"].sudo().search_count([("user_id", "=", uid)], limit=1)
                    or self.env["bf.work.welcome"].sudo().search_count([("user_id", "=", uid)], limit=1))

    @api.model
    def bf_welcome_skip(self):
        """"Go on without it" on the first screen: not shown again, nothing turned on."""
        # A public method, callable over RPC by any user: internal users only.
        if not self.env.user._is_internal():
            raise AccessError(_("Only internal users can do this."))
        if self._bf_needs_welcome():
            self.env["bf.work.welcome"].sudo().create({"user_id": self.env.uid})
        return self.env["ir.actions.act_window"]._for_xml_id("bf_work_hours.action_bf_work_day_mine")
