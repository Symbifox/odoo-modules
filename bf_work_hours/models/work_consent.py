from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .tools import acting_in_own_web_session, own_record_vals

# Version of the notice shown before activation (art. 8.1 of Quebec's private
# sector Act). Raise it when the notice says something new: a consent given
# under an older notice stays valid, and keeps the text it was given under.
# 2: the Gen source names the passes started with a button (BF Working Hours - Gen 18.0.1.0.1).
NOTICE_VERSION = "2"

SCOPES = [
    ("hours", "Propose my hours"),
    ("insights", "My indicators"),
]


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
        if not self.env.su:
            self._bf_check_actor()
            Wizard = self.env["bf.work.activate.wizard"]
            for vals in vals_list:
                own_record_vals(self.env, vals, "user_id",
                                _("Only the person concerned can activate this feature, now."))
                # An activation is dated now, under the current notice, written
                # by the server: none of these are the caller's to set.
                for forced in ("withdrawn_at", "granted_at", "notice_version"):
                    vals.pop(forced, None)
                vals["notice_text"] = Wizard._bf_notice(vals.get("scope"))
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
                if consent.scope == "insights" and consent.user_id.id not in hours:
                    raise UserError(_("Turn on “Propose my hours” first: the indicators are computed from it."))
        consents._bf_work_changed()
        return consents

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
                self.search([("user_id", "=", user.id), ("scope", "=", "insights"),
                             ("withdrawn_at", "=", False)]).write({"withdrawn_at": when})
                user._bf_work_forget(["hours", "insights"])
            else:
                user._bf_work_forget(["insights"])

    def unlink(self):
        if not self.env.su:
            raise UserError(_("An activation is kept as evidence: withdraw it instead."))
        return super().unlink()

    def _bf_work_changed(self):
        """Activations changed: the preferences read a computed field cached for the transaction."""
        self.env["res.users"].invalidate_model(["bf_work_hours_enabled", "bf_work_insights_enabled"])

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
