# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfOutreachLogWizard(models.TransientModel):
    _name = "bf.outreach.log.wizard"
    _description = "Log an outreach touch"

    target_ids = fields.Many2many(
        "bf.outreach.target",
        string="Targets",
        required=True,
    )
    target_count = fields.Integer(
        string="Number of targets", compute="_compute_target_count"
    )
    date = fields.Datetime(string="Date", default=fields.Datetime.now, required=True)
    kind = fields.Selection(
        [
            ("call", "Call"),
            ("email", "Email"),
            ("letter", "Letter"),
            ("sms", "Text message"),
            ("meeting", "Meeting"),
            ("linkedin", "LinkedIn"),
            ("other", "Other"),
        ],
        string="Type",
        default="call",
        required=True,
    )
    direction = fields.Selection(
        [("out", "Outgoing"), ("in", "Incoming")],
        string="Direction",
        default="out",
        required=True,
    )
    outcome = fields.Selection(
        [
            ("no_answer", "No answer"),
            ("voicemail", "Voicemail"),
            ("reached", "Person reached"),
            ("replied", "Reply received"),
            ("interested", "Interest shown"),
            ("not_interested", "Declined"),
            ("callback", "Callback requested"),
            ("bounced", "Invalid address"),
            ("sent", "Sent"),
        ],
        string="Outcome",
        default="no_answer",
        required=True,
    )
    duration = fields.Float(string="Duration (min)")
    summary = fields.Char(string="Summary")
    note = fields.Html(string="Details")
    stage_id = fields.Many2one(
        "bf.outreach.stage",
        string="Move to stage",
        help="Leave empty to keep the current stage.",
    )
    closed_reason = fields.Selection(
        [
            ("converted", "Won over"),
            ("not_interested", "Not interested"),
            ("no_answer", "Never reached"),
            ("bad_timing", "Bad timing"),
            ("invalid", "Invalid contact details"),
            ("competitor", "Already served elsewhere"),
            ("other", "Other"),
        ],
        string="Closing reason",
    )
    snooze_days = fields.Integer(
        string="Postpone by (days)",
        help="Shifts the next follow-up, for instance when the target "
             "asks to be called back later. 0 = normal cadence.",
    )

    @api.depends("target_ids")
    def _compute_target_count(self):
        for wizard in self:
            wizard.target_count = len(wizard.target_ids)

    @api.onchange("kind")
    def _onchange_kind(self):
        if self.kind in ("email", "letter"):
            self.outcome = "sent"
        elif self.kind == "call":
            self.outcome = "no_answer"

    def action_log(self):
        self.ensure_one()
        if not self.target_ids:
            raise UserError(_("No target selected."))
        touch_values = []
        for target in self.target_ids:
            touch_values.append(
                {
                    "target_id": target.id,
                    "date": self.date,
                    "kind": self.kind,
                    "direction": self.direction,
                    "outcome": self.outcome,
                    "duration": self.duration,
                    "summary": self.summary,
                    "note": self.note,
                    "user_id": self.env.user.id,
                }
            )
        self.env["bf.outreach.touch"].create(touch_values)

        target_values = {}
        if self.stage_id:
            target_values["stage_id"] = self.stage_id.id
        if self.closed_reason:
            target_values["closed_reason"] = self.closed_reason
        if self.snooze_days:
            target_values["paused_until"] = fields.Date.context_today(self) + timedelta(
                days=self.snooze_days
            )
        if target_values:
            self.target_ids.write(target_values)
        return {"type": "ir.actions.act_window_close"}
