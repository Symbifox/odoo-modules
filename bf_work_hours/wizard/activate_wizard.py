from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import format_date

from ..models.tools import acting_in_own_web_session
from ..models.work_collector import LIVE_SOURCES
from ..models.work_consent import HISTORY_SPANS, SCOPES


class WorkActivateWizard(models.TransientModel):
    """The notice of art. 8.1, shown BEFORE the feature turns on.

    The person reads what is collected, who sees it, how long it is kept and
    how to turn it off, then turns it on themselves. The notice is kept with
    the activation, word for word.
    """

    _name = "bf.work.activate.wizard"
    _description = "Working time: activation notice"
    # bf_impersonate hides this from whoever impersonates the person.
    _gen_scope = "bf_work_hours"

    scope = fields.Selection(SCOPES, required=True, default="hours")
    # With the hours, or alone for "My earlier days": how far back to take in.
    history_span = fields.Selection([("none", "From now on")] + HISTORY_SPANS, string="How far back", default="none")
    notice = fields.Html(compute="_compute_notice", sanitize=False)
    understood = fields.Boolean(string="I have read this notice and I turn the feature on myself")

    @api.depends("scope", "history_span")
    def _compute_notice(self):
        for wizard in self:
            notice = wizard._bf_notice(wizard.scope, wizard.history_span)
            if wizard.scope == "hours" and wizard.history_span in dict(HISTORY_SPANS):
                notice += wizard._bf_notice("history", wizard.history_span)
            wizard.notice = notice

    @api.model
    def _bf_notice(self, scope, span=None):
        """The notice of a scope, as shown and as kept with the activation, word for word."""
        if scope == "history":
            return self._bf_history_notice(span)
        ICP = self.env["ir.config_parameter"].sudo()
        signal_days = ICP.get_param("bf_work_hours.signal_retention_days", 60)
        day_days = ICP.get_param("bf_work_hours.day_retention_days", 400)
        gap = self.env["bf.work.day"]._bf_gap_minutes()
        visibility = Markup("").join(
            Markup("<p>%s</p>") % text for text in self.env["bf.work.collector"]._bf_notice_visibility())
        sources = self._bf_notice_sources()
        parts = [
            Markup('<p class="o_bf_work_notice_lead">%s</p>') % _(
                "This feature uses a technology that can profile you: it evaluates how you work "
                "from traces of your activity. It is off until you turn it on, and refusing it has "
                "no consequence for you."),
        ]
        if scope == "hours":
            parts += [
                Markup("<h4>%s</h4><p>%s</p><ul>%s</ul>") % (
                    _("What is read"),
                    _("From the moment you turn it on, Odoo reads these traces of yours only:"),
                    sources),
                Markup("<p>%s</p>") % _(
                    "It does not read the chatter or the changes made under your name: an agent or a "
                    "script using your account could have written them."),
                Markup("<p>%s</p>") % _(
                    "Your days from before are not read, unless you choose to take them in: a separate "
                    "choice, with its own notice, that you can turn off on its own."),
                Markup("<h4>%s</h4><p>%s</p>") % (
                    _("What it gives you"),
                    _("Your reconstructed working time, day by day, with a low and a high bound. Two traces "
                      "less than %(gap)s minutes apart count as one stretch of work. Time spent away from "
                      "the screen is not seen: the figure is an estimate, not your timesheet.", gap=gap)),
            ]
        else:
            parts += [
                Markup("<h4>%s</h4><p>%s</p>") % (
                    _("What it adds"),
                    _("For each day: the span from your first to your last trace, your longest stretch, "
                      "your time in meetings, meetings without a 10-minute break, and your time outside "
                      "your working schedule. No score, no ranking.")),
                Markup("<p>%s</p>") % _(
                    "If you take in your earlier days, those days get these figures too."),
            ]
        parts += [
            Markup("<h4>%s</h4><p>%s</p>") % (
                _("Who sees it"),
                _("You only, in Odoo's screens: managers and administrators do not see it there. You may "
                  "share your days with a colleague you name, and revoke it at any time; the raw traces are "
                  "never shared. A system administrator who changes the security settings or runs code on "
                  "the server could reach these data, as with any data in Odoo.")) + visibility,
            Markup("<h4>%s</h4><p>%s</p>") % (
                _("What it is not"),
                _("Nothing is decided automatically from it. Your employer's record of your working hours "
                  "remains the timesheet you enter and approve.")),
            Markup("<h4>%s</h4><p>%s</p>") % (
                _("How long it is kept"),
                _("Raw traces: %(signal_days)s days. Days: %(day_days)s days.",
                  signal_days=signal_days, day_days=day_days)),
            Markup("<h4>%s</h4><p>%s</p>") % (
                _("Turning it off"),
                _("At any time, from Working Time > My choices. Turning off “Propose my hours” destroys your "
                  "traces and your days, and ends your shares.")),
        ]
        return self._bf_sections(scope, parts)

    @api.model
    def _bf_sections(self, scope, parts):
        """One section per heading, so that the screen can lay the notice out; the words
        are the notice's, and they are what is kept with the activation."""
        out, current = [], []
        for part in parts:
            if str(part).startswith("<h4>") and current:
                out.append(current)
                current = []
            current.append(part)
        out.append(current)
        body = Markup("").join(
            Markup('<section class="o_bf_work_notice_part">%s</section>') % Markup("").join(chunk) for chunk in out)
        return Markup('<div class="o_bf_work_notice o_bf_work_notice_%s">%s</div>') % (scope, body)

    @api.model
    def _bf_notice_sources(self, keys=None):
        return Markup("").join(
            # A line break, not a colon: French and English do not space a colon alike.
            Markup("<li><b>%s</b><br/>%s</li>") % (label, text)
            for key, label, text in self.env["bf.work.collector"]._bf_source_descriptions()
            if keys is None or key in keys
        )

    @api.model
    def _bf_history_start_label(self, span):
        """The first day "My earlier days" would read, in the reader's time zone."""
        start = self.env["bf.work.consent"]._bf_history_start(span)
        if not start:
            return ""
        Day = self.env["bf.work.day"]
        return format_date(self.env, Day._bf_local_date(start, Day._bf_tz(self.env["res.users"].browse(self.env.uid))))

    @api.model
    def _bf_history_notice(self, span):
        """The notice of "My earlier days", for the span the person picks."""
        if span not in dict(HISTORY_SPANS):
            return Markup("")
        ICP = self.env["ir.config_parameter"].sudo()
        day_days = ICP.get_param("bf_work_hours.day_retention_days", 400)
        signal_days = ICP.get_param("bf_work_hours.signal_retention_days", 60)
        descriptions = self.env["bf.work.collector"]._bf_source_descriptions()
        again = [key for key, _label, _text in descriptions if key not in LIVE_SOURCES]
        live = [label for key, label, _text in descriptions if key in LIVE_SOURCES]
        reach = (_("the last 30 days") if span == "30" else _("all the history kept"))
        parts = [
            Markup('<p class="o_bf_work_notice_lead">%s</p>') % _(
                "Taking in your earlier days is a choice of its own, apart from your hours: you can turn it off "
                "alone, and refusing it has no consequence for you."),
            Markup("<h4>%s</h4><p>%s</p><ul>%s</ul>") % (
                _("What is read again"),
                _("The same traces as for your hours, from %(date)s (%(reach)s) instead of the moment you "
                  "turned your hours on:", date=self._bf_history_start_label(span), reach=reach),
                self._bf_notice_sources(again)),
        ]
        # How far back each source really reaches, where it differs from the span chosen.
        if "calendar" in again:
            parts.append(Markup("<p>%s</p>") % _(
                "A calendar answer given before this feature was installed counts for the hours the meeting "
                "had at the installation."))
        if "terminal" in again:
            parts.append(Markup("<p>%s</p>") % _(
                "Your own tools reach back no further than the raw traces are kept: %(signal_days)s days.",
                signal_days=signal_days))
        if live:
            parts.append(Markup("<p>%s</p>") % _(
                "Not read again, since they are captured only once your hours are on: %(sources)s. What you did "
                "in Odoo before then counts only through the sources above.",
                sources=", ".join(label[:1].lower() + label[1:] for label in live)))
        parts += [
            Markup("<h4>%s</h4><p>%s</p>") % (
                _("Why a separate choice"),
                _("These traces were first recorded for other purposes. Reading them again to reconstruct "
                  "your working time is a new use: it needs your own consent.")),
            Markup("<h4>%s</h4><p>%s</p>") % (
                _("What it gives you"),
                _("Your days from before, reconstructed the same way and with the same bounds, and your "
                  "indicators for those days if you turned them on. They appear a slice at a time, within "
                  "minutes.")),
            Markup("<h4>%s</h4><p>%s</p>") % (
                _("Who sees it"),
                _("The same people as your other days: you, and the colleagues you share your days with. A system "
                  "administrator who changes the security settings or runs code on the server could reach these "
                  "data, as with any data in Odoo."))
            + Markup("").join(Markup("<p>%s</p>") % text
                              for text in self.env["bf.work.collector"]._bf_notice_visibility()),
            Markup("<h4>%s</h4><p>%s</p>") % (
                _("How long it is kept"),
                _("Days: %(day_days)s days. “All the history kept” goes back that far, and no further.",
                  day_days=day_days)),
            Markup("<h4>%s</h4><p>%s</p>") % (
                _("Turning it off"),
                _("At any time, from Working Time > My choices: the days from before your hours were turned on "
                  "are destroyed; the days since stay.")),
        ]
        return self._bf_sections("history", parts)

    @api.model
    def bf_screen_data(self, scope="hours"):
        """What the consent screen shows the reader: the notices, and their own state.

        The notices are the ones the server writes and keeps with an activation.
        The state is the reader's only, and only in their own web session (not
        through an API key, not to someone impersonating them).
        """
        # A public method, callable over RPC by any user: internal users only.
        if not self.env.user._is_internal():
            raise AccessError(_("Only internal users can do this."))
        if scope not in dict(SCOPES):
            raise UserError(_("Unknown choice."))
        mine = not self.env.su and acting_in_own_web_session(self.env)
        Consent = self.env["bf.work.consent"].sudo()
        uid = self.env.uid
        state = {key: mine and uid in Consent._bf_active_user_ids(key) for key, _label in SCOPES}
        since = Consent._bf_since(self.env["res.users"].browse(uid), "hours") if state["hours"] else None
        titles = {
            "hours": (_("Your working time, seen by you first"),
                      _("Odoo can reconstruct the time you really work from your own traces, and show it to you. "
                        "Read what it reads and who sees it, then decide.")),
            "insights": (_("Your indicators"),
                         _("The span of your days, your longest stretch, your meetings and your time outside "
                           "working hours, for you. Read the notice, then decide.")),
            "history": (_("Your earlier days"),
                        _("Take in the days before you turned your hours on, from the same traces. Read the notice, "
                          "then decide.")),
        }
        history = []
        if scope in ("hours", "history"):
            if scope == "hours":
                history.append({"span": "none", "label": _("From now on"),
                                "hint": _("Only what happens from today"), "notice": "", "available": True})
            for span, _label in HISTORY_SPANS:
                start = Consent._bf_history_start(span)
                history.append({
                    "span": span,
                    "label": _("The last 30 days") if span == "30" else _("All the history kept"),
                    "hint": _("From %(date)s", date=self._bf_history_start_label(span)),
                    "notice": str(self._bf_history_notice(span)),
                    # Further back than the hours, or it adds nothing.
                    "available": not since or start < since,
                })
        title, subtitle = titles[scope]
        return {
            "scope": scope,
            "can_act": mine,
            "state": state,
            "title": title,
            "subtitle": subtitle,
            "promises": [_("Off until you turn it on"), _("Seen by you, and the colleagues you name"),
                         _("Nothing decided automatically")],
            "notice": str(self._bf_notice(scope)) if scope != "history" else "",
            "history": history,
        }

    def action_confirm(self):
        self.ensure_one()
        if not self.understood:
            raise UserError(_("Tick the box to confirm you have read the notice."))
        Consent = self.env["bf.work.consent"]
        user = self.env.user
        hours = Consent._bf_active_user_ids("hours")
        if self.scope == "insights" and user.id not in hours:
            raise UserError(_("Turn on “Propose my hours” first: the indicators are computed from it."))
        if self.scope == "history" and user.id not in hours:
            raise UserError(_("Turn on “Propose my hours” first: your earlier days are taken in with it."))
        span = self.history_span if self.history_span in dict(HISTORY_SPANS) else False
        if self.scope == "history" and not span:
            raise UserError(_("Choose how far back your earlier days are taken in."))
        if user.id not in Consent._bf_active_user_ids(self.scope):
            Consent.create({"user_id": user.id, "scope": self.scope,
                            "history_span": span if self.scope == "history" else False})
        if self.scope == "hours" and span and user.id not in Consent._bf_active_user_ids("history"):
            # Chosen on the same screen: a second activation, with its own notice.
            Consent.create({"user_id": user.id, "scope": "history", "history_span": span})
        return self._bf_next()

    def _bf_next(self):
        """Only where to go next: a full reload, which starts the browser activity again."""
        if self.env.context.get("bf_work_back_to_choices"):
            # The page shows the person's state, read again.
            return self.env["bf.work.choices"]._bf_page()
        if self.env.context.get("bf_work_back_to_mine"):
            action = self.env.ref("bf_work_hours.action_bf_work_day_mine")
            return {"type": "ir.actions.client", "tag": "reload", "params": {"action_id": action.id}}
        return {"type": "ir.actions.client", "tag": "reload"}
