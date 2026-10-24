from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .tools import acting_in_own_web_session, own_record_vals

# Version of the notice shown before activation (art. 8.1 of Quebec's private
# sector Act). Raise it when the notice says something new: a consent given
# under an older notice stays valid, and keeps the text it was given under.
# 2: the Gen source names the passes started with a button (BF Working Hours - Gen 18.0.1.0.1).
# 3: the person may take in their earlier days, a separate choice with its own notice (18.0.1.1.0).
NOTICE_VERSION = "3"

SCOPES = [
    ("hours", "Propose my hours"),
    ("insights", "My indicators"),
    ("history", "My earlier days"),
]
# How far back "My earlier days" reads, chosen by the person. "all" goes back as far as
# a day is kept (bf_work_hours.day_retention_days): further would be destroyed anyway.
HISTORY_SPANS = [
    ("30", "The last 30 days"),
    ("all", "All the history kept"),
]
HISTORY_SPAN_DAYS = {"30": 30}


class WorkConsent(models.Model):
    """One activation by one person, kept as evidence after it is withdrawn.

    Off by default, turned on by the person ONLY: reading working-time traces
    to evaluate work is profiling (art. 8.1 names "work performance"), and the
    Commission d'accès à l'information reads 8.1 as "off by default, switched
    on by the person concerned". Enforced at three levels:

    - the record rules: nobody reads, creates or withdraws another person's
      activation in Odoo's screens and RPC, administrators included;
    - the person must act in their OWN web session: not through an API key
      (an agent or a script holding it), not under an impersonation;
    - the notice stored is the one the server writes, not one sent by a client.
    """

    _name = "bf.work.consent"
    _description = "Working time: activation by a person"
    _order = "granted_at desc, id desc"
    # bf_impersonate hides these from whoever impersonates the person.
    _gen_scope = "bf_work_hours"

    user_id = fields.Many2one(
        "res.users", string="Person", required=True, readonly=True, index=True,
        ondelete="cascade", default=lambda self: self.env.user,
    )
    scope = fields.Selection(SCOPES, required=True, readonly=True)
    granted_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    notice_version = fields.Char(required=True, readonly=True, default=NOTICE_VERSION)
    notice_text = fields.Html(
        readonly=True, sanitize=True,
        help="The notice exactly as it was shown when the person activated the feature.",
    )
    withdrawn_at = fields.Datetime(readonly=True)
    is_active = fields.Boolean(compute="_compute_is_active", store=True)
    # "My earlier days" only: how far back the person chose, and the moment it reaches,
    # both written by the server at the activation. Traces from before the hours are
    # read only while this activation lasts.
    history_span = fields.Selection(HISTORY_SPANS, string="How far back", readonly=True)
    read_from = fields.Datetime(string="Reads from", readonly=True)
    # Progress of taking in the earlier days: every day from this date to the
    # activation of the hours is computed. Written by the job, as superuser.
    computed_from = fields.Date(string="Taken in back to", readonly=True)

    def init(self):
        # One live activation per person and scope: a second one, born of a race,
        # fails here instead of piling up.
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS bf_work_consent_live_unique "
            "ON bf_work_consent (user_id, scope) WHERE withdrawn_at IS NULL")

    @api.depends("withdrawn_at")
    def _compute_is_active(self):
        for consent in self:
            consent.is_active = not consent.withdrawn_at

    def _bf_check_actor(self):
        if not self.env.su and not acting_in_own_web_session(self.env):
            raise AccessError(_("Only the person concerned can do this, in their own session: "
                                "not through an API key, not while being impersonated."))

    @api.model_create_multi
    def create(self, vals_list):
        # A withdrawal still in the cache would leave the live index holding the old row.
        self.flush_model(["withdrawn_at"])
        if not self.env.su:
            self._bf_check_actor()
            Wizard = self.env["bf.work.activate.wizard"]
            for vals in vals_list:
                own_record_vals(self.env, vals, "user_id",
                                _("Only the person concerned can activate this feature, now."))
                # 🔴 Named in vals: the notice kept is chosen from it below, and a
                # `default_scope` of the context would give the record another scope.
                if vals.get("scope") not in dict(SCOPES):
                    raise UserError(_("Unknown choice."))
                # An activation is dated now, under the current notice, written
                # by the server: none of these are the caller's to set.
                for forced in ("withdrawn_at", "granted_at", "notice_version"):
                    vals.pop(forced, None)
                # Explicit values, so that no `default_*` of the context fills them.
                span = vals.get("history_span") if vals.get("scope") == "history" else False
                vals["history_span"] = span
                vals["read_from"] = self._bf_history_start(span) if span else False
                vals["computed_from"] = False
                vals["notice_text"] = Wizard._bf_notice(vals.get("scope"), span)
        consents = super().create(vals_list)
        if not self.env.su:
            # 🔴 A guard on vals alone is bypassed by `default_*` in the context
            # and by ir.default: judge the record as created.
            now = fields.Datetime.now()
            hours = self._bf_active_user_ids("hours")
            for consent in consents:
                if (consent.user_id.id != self.env.uid or consent.withdrawn_at
                        or consent.notice_version != NOTICE_VERSION
                        or abs((consent.granted_at - now).total_seconds()) > 60):
                    raise AccessError(_("Only the person concerned can activate this feature, now."))
                if consent.computed_from:
                    raise AccessError(_("Only the person concerned can activate this feature, now."))
                if consent.scope == "history":
                    expected = consent.history_span and self._bf_history_start(consent.history_span)
                    if not expected or not consent.read_from \
                            or abs((consent.read_from - expected).total_seconds()) > 60:
                        raise UserError(_("Choose how far back your earlier days are taken in."))
                    # Further back than the hours, or it adds nothing (the screen greys it out).
                    hours_since = self._bf_since(consent.user_id, "hours")
                    if hours_since and consent.read_from >= hours_since:
                        raise UserError(_("There is nothing to take in: your hours were already on at the start "
                                          "of the span you chose."))
                elif consent.history_span or consent.read_from:
                    raise AccessError(_("Only the person concerned can activate this feature, now."))
                if consent.scope in ("insights", "history") and consent.user_id.id not in hours:
                    raise UserError(_("Turn on “Propose my hours” first: the indicators are computed from it.")
                                    if consent.scope == "insights" else
                                    _("Turn on “Propose my hours” first: your earlier days are taken in with it."))
        consents._bf_work_changed()
        take_in = any(c.scope == "history" for c in consents)
        insights = consents.filtered(lambda c: c.scope == "insights")
        if insights:
            # The indicators reach the earlier days too (their notice says so), and the
            # hourly job redoes only the last days: the job takes them in again, from the
            # start. Written even when already empty, so that a slice the job is computing
            # without the indicators cannot record its progress over this.
            again = self.sudo().search([("user_id", "in", insights.user_id.ids), ("scope", "=", "history"),
                                        ("withdrawn_at", "=", False)])
            again.write({"computed_from": False})
            take_in = take_in or bool(again)
        if take_in:
            # Taken in by the job, a slice at a time, starting now.
            self.env.ref("bf_work_hours.cron_bf_work_history").sudo()._trigger()
        return consents

    @api.model
    def _bf_history_start(self, span):
        """The moment "My earlier days" reads from, for a span chosen now."""
        if span == "all":
            days = int(self.env["ir.config_parameter"].sudo().get_param("bf_work_hours.day_retention_days", 400))
        else:
            days = HISTORY_SPAN_DAYS.get(span)
        if not days:
            return False
        return fields.Datetime.now() - timedelta(days=max(days, 1))

    def write(self, vals):
        if not self.env.su:
            self._bf_check_actor()
            if set(vals) - {"withdrawn_at"}:
                raise UserError(_("An activation cannot be changed, only withdrawn."))
            if not vals.get("withdrawn_at") or any(c.withdrawn_at for c in self):
                raise UserError(_("An activation can be withdrawn once, and never restored."))
            if any(c.user_id.id != self.env.uid for c in self):
                raise AccessError(_("Only the person concerned can withdraw this activation."))
            # Withdrawn now, whatever date the caller sends.
            vals = dict(vals, withdrawn_at=fields.Datetime.now())
        res = super().write(vals)
        if vals.get("withdrawn_at"):
            self._bf_work_changed()
            self.sudo()._bf_after_withdrawal(vals["withdrawn_at"])
        return res

    def _bf_after_withdrawal(self, when):
        """What a withdrawal destroys, whatever path it took (preferences, RPC, register).

        Hours: the indicators go too (they are computed from the hours), then
        every trace, every day and every share of the person. Indicators: their
        figures are wiped from the days, the hours stay.
        """
        for consent in self:
            user = consent.user_id
            if consent.scope == "hours":
                # The other two rest on the hours: they end with them.
                self.search([("user_id", "=", user.id), ("scope", "in", ("insights", "history")),
                             ("withdrawn_at", "=", False)]).write({"withdrawn_at": when})
                user._bf_work_forget(["hours", "insights", "history"])
            else:
                user._bf_work_forget([consent.scope])

    def unlink(self):
        if not self.env.su:
            raise UserError(_("An activation is kept as evidence: withdraw it instead."))
        return super().unlink()

    def _bf_work_changed(self):
        """Activations changed: the preferences read a computed field cached for the transaction."""
        self.env["res.users"].invalidate_model(["bf_work_hours_enabled", "bf_work_insights_enabled",
                                                "bf_work_history_enabled"])

    @api.model
    def _bf_active_user_ids(self, scope):
        """Ids of the people with an active consent for this scope (any company).

        🔴 NOT cached. An ormcache here raced under load: a heartbeat request
        that began before an activation stored the old list AFTER the
        activation cleared the cache, and the activation stayed invisible (a
        second one was created). One indexed query a minute per person is cheap.
        """
        # 🔴 Raw SQL reads the database, not the ORM's pending writes: a
        # withdrawal made in this transaction must be flushed to be seen.
        self.flush_model(["user_id", "scope", "withdrawn_at"])
        self.env.cr.execute(
            "SELECT DISTINCT c.user_id FROM bf_work_consent c JOIN res_users u ON u.id = c.user_id "
            "WHERE c.scope = %s AND c.withdrawn_at IS NULL AND u.active",
            [scope],
        )
        return frozenset(row[0] for row in self.env.cr.fetchall())

    @api.model
    def _bf_since(self, user, scope):
        """When the person turned this scope on (the current activation), or None."""
        consent = self.sudo().search([("user_id", "=", user.id), ("scope", "=", scope),
                                      ("withdrawn_at", "=", False)], order="granted_at desc", limit=1)
        return consent.granted_at or None

    @api.model
    def _bf_history(self, user):
        """The person's live "My earlier days" activation, if their hours are on too."""
        if not self._bf_since(user, "hours"):
            return self.browse()
        return self.sudo().search([("user_id", "=", user.id), ("scope", "=", "history"),
                                   ("withdrawn_at", "=", False)], order="granted_at desc", limit=1)

    @api.model
    def _bf_read_from(self, user):
        """The earliest moment the person's traces are read: the activation of the hours,
        or further back if they chose to take in their earlier days. None if off."""
        since = self._bf_since(user, "hours")
        if not since:
            return None
        history = self._bf_history(user)
        return min(since, history.read_from) if history.read_from else since
