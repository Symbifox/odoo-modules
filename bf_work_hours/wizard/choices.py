from odoo import _, api, fields, models


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

    @api.depends_context("uid")
    def _compute_enabled(self):
        hours, insights = self._bf_enabled("hours"), self._bf_enabled("insights")
        for choices in self:
            choices.hours_enabled = hours
            choices.insights_enabled = insights

    def _compute_display_name(self):
        for choices in self:
            choices.display_name = _("My choices")

    @api.model
    def default_get(self, fields_list):
        # 🔴 The first onchange of a new record puts False in every field without
        # a default, computed or not: the switches showed "off" and the title
        # "New". What the page shows when it opens comes from here.
        res = super().default_get(fields_list)
        page = {"display_name": _("My choices"),
                "hours_enabled": self._bf_enabled("hours"),
                "insights_enabled": self._bf_enabled("insights")}
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
        if result.get("res_model") == "bf.work.activate.wizard":
            # The notice brings the person back here once they turned it on.
            result["context"] = dict(result.get("context") or {}, bf_work_back_to_choices=True)
        return result

    def action_activate_hours(self):
        return self._act("action_bf_work_activate", "hours")

    def action_activate_insights(self):
        return self._act("action_bf_work_activate", "insights")

    def action_withdraw_hours(self):
        return self._act("action_bf_work_withdraw", "hours")

    def action_withdraw_insights(self):
        return self._act("action_bf_work_withdraw", "insights")
