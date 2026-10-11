from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.work_consent import SCOPES


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
    notice = fields.Html(compute="_compute_notice", sanitize=False)
    understood = fields.Boolean(string="I have read this notice and I turn the feature on myself")

    @api.depends("scope")
    def _compute_notice(self):
        for wizard in self:
            wizard.notice = wizard._bf_notice(wizard.scope)

    @api.model
    def _bf_notice(self, scope):
        ICP = self.env["ir.config_parameter"].sudo()
        signal_days = ICP.get_param("bf_work_hours.signal_retention_days", 60)
        day_days = ICP.get_param("bf_work_hours.day_retention_days", 400)
        gap = self.env["bf.work.day"]._bf_gap_minutes()
        visibility = Markup("").join(
            Markup("<p>%s</p>") % text for text in self.env["bf.work.collector"]._bf_notice_visibility())
        sources = Markup("").join(
            # A line break, not a colon: French and English do not space a colon alike.
            Markup("<li><b>%s</b><br/>%s</li>") % (label, text)
            for _key, label, text in self.env["bf.work.collector"]._bf_source_descriptions()
        )
        parts = [
            Markup("<p>%s</p>") % _(
                "This feature uses a technology that can profile you: it evaluates how you work "
                "from traces of your activity. It is off until you turn it on, and refusing it has "
                "no consequence for you."),
        ]
        if scope == "hours":
            parts += [
                Markup("<h4>%s</h4><p>%s</p><ul>%s</ul>") % (
                    _("What is read"),
                    _("From the day you turn it on, and never before, Odoo reads these traces of yours only:"),
                    sources),
                Markup("<p>%s</p>") % _(
                    "It does not read the chatter or the changes made under your name: an agent or a "
                    "script using your account could have written them."),
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
        return Markup("").join(parts)

    def action_confirm(self):
        self.ensure_one()
        if not self.understood:
            raise UserError(_("Tick the box to confirm you have read the notice."))
        Consent = self.env["bf.work.consent"]
        user = self.env.user
        if self.scope == "insights" and user.id not in Consent._bf_active_user_ids("hours"):
            raise UserError(_("Turn on “Propose my hours” first: the indicators are computed from it."))
        if user.id in Consent._bf_active_user_ids(self.scope):
            return {"type": "ir.actions.act_window_close"}
        Consent.create({"user_id": user.id, "scope": self.scope})
        if self.env.context.get("bf_work_back_to_choices"):
            # Only where to go next: the page shows the person's state, read again.
            return self.env["bf.work.choices"]._bf_page()
        return {"type": "ir.actions.client", "tag": "reload"}
