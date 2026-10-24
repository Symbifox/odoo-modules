from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .tools import acting_in_own_web_session
from .work_day import INSIGHT_FIELDS

WORK_SELF_FIELDS = ["bf_work_hours_enabled", "bf_work_insights_enabled", "bf_work_history_enabled"]


class ResUsers(models.Model):
    _inherit = "res.users"

    # 🔴 Every internal and portal user can read res.users: these fields say
    # something only about the reader, and are empty for anyone else's record.
    bf_work_hours_enabled = fields.Boolean(
        string="Propose my hours", compute="_compute_bf_work_enabled",
        help="Odoo reconstructs your working time from your own traces and shows it to you, and to the colleagues you name.",
    )
    bf_work_insights_enabled = fields.Boolean(
        string="My indicators", compute="_compute_bf_work_enabled",
        help="Span of the day, longest stretch, meetings, time outside working hours: for you, and for the colleagues you name.",
    )
    bf_work_history_enabled = fields.Boolean(
        string="My earlier days", compute="_compute_bf_work_enabled",
        help="Your days from before you turned your hours on, taken in from the same traces.",
    )
    # The days' record rule goes through this relation, evaluated by the database
    # on every query: a rule that baked user ids into its domain would be cached
    # by the registry, and a revoked colleague could keep reading for a while.
    bf_work_share_ids = fields.One2many("bf.work.share", "owner_id", string="Shares of my days")
    bf_work_shared_owner_ids = fields.Many2many(
        "res.users", string="People who share their days with me",
        compute="_compute_bf_work_shared_owner_ids",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + WORK_SELF_FIELDS

    def _bf_work_is_reader(self):
        """True for the reader's own record, read in their own web session.

        Not under an impersonation (the impersonator would see the person's
        state and who shares with them), and not for superuser code reading
        res.users on someone's behalf: server code asks the consents directly.
        """
        return self.id == self.env.uid and acting_in_own_web_session(self.env)

    @api.depends_context("uid")
    def _compute_bf_work_enabled(self):
        Consent = self.env["bf.work.consent"].sudo()
        hours = Consent._bf_active_user_ids("hours")
        insights = Consent._bf_active_user_ids("insights")
        history = Consent._bf_active_user_ids("history")
        for user in self:
            mine = user._bf_work_is_reader()
            user.bf_work_hours_enabled = mine and user.id in hours
            user.bf_work_insights_enabled = mine and user.id in insights
            user.bf_work_history_enabled = mine and user.id in history

    @api.depends_context("uid")
    def _compute_bf_work_shared_owner_ids(self):
        Share = self.env["bf.work.share"].sudo()
        for user in self:
            owners = Share._bf_owner_ids_for(user.id) if user._bf_work_is_reader() else []
            user.bf_work_shared_owner_ids = [(6, 0, owners)]

    def _bf_work_check_self(self):
        if self.env.su:
            return
        if len(self) != 1 or self.id != self.env.uid or not acting_in_own_web_session(self.env):
            raise AccessError(_("Only the person concerned can turn this on or off, in their own session."))

    def action_bf_work_activate(self):
        """Open the consent screen; the feature turns on only from its notice."""
        self._bf_work_check_self()
        scope = self.env.context.get("bf_work_scope", "hours")
        return {
            "type": "ir.actions.client",
            "tag": "bf_work_hours.consent",
            "target": "current",
            "name": _("Before you turn it on"),
            # Once turned on, back to My choices, where the switches show it.
            "params": {"scope": scope, "mode": "activate"},
        }

    def action_bf_work_withdraw(self):
        """Turn a feature off. The withdrawal itself destroys what it must."""
        self._bf_work_check_self()
        scope = self.env.context.get("bf_work_scope", "hours")
        consents = self.env["bf.work.consent"].search([
            ("user_id", "=", self.id), ("scope", "=", scope), ("withdrawn_at", "=", False)])
        if consents:
            consents.write({"withdrawn_at": fields.Datetime.now()})
        return {"type": "ir.actions.client", "tag": "reload"}

    def _bf_work_forget(self, scopes):
        """What a withdrawal destroys: everything for hours, the indicators for insights,
        the days before the activation of the hours for the earlier days."""
        self.ensure_one()
        Day = self.env["bf.work.day"].sudo()
        if "hours" in scopes:
            self.env["bf.work.signal"].sudo().search([("user_id", "=", self.id)]).unlink()
            self.env["bf.work.share"].sudo().search([("owner_id", "=", self.id), ("active", "=", True)]
                                                    ).action_revoke()
            Day.search([("user_id", "=", self.id)]).unlink()
            return
        if "history" in scopes:
            Day._bf_forget_before_activation(self)
        if "insights" in scopes:
            # The indicators of the days still kept (the earlier days were taken in with them).
            Day.search([("user_id", "=", self.id)]).write(
                dict.fromkeys(INSIGHT_FIELDS, False) | {"insights_included": False})
