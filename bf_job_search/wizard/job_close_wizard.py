# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.exceptions import UserError

from ..models.job_application import OUTCOMES


class JobCloseWizard(models.TransientModel):
    _name = "bf.job.close.wizard"
    _description = "Close a job application"

    application_ids = fields.Many2many("bf.job.application", string="Applications", required=True)
    outcome = fields.Selection(OUTCOMES, string="Closed as", required=True, default="rejected")
    note = fields.Text("Note")
    ask_feedback = fields.Boolean(
        "Remind me to ask for feedback", default=True,
        help="After a refusal: a short, polite request for what would have made the "
             "application stronger.")

    def action_close(self):
        self.ensure_one()
        apps = self.application_ids
        if not apps:
            raise UserError(self.env._("There is no application of yours to close."))
        apps.check_access("write")
        if apps.filtered("is_closed"):
            raise UserError(self.env._("One of these applications is already closed."))
        apps._close(self.outcome, note=self.note, ask_feedback=self.ask_feedback)
        return {"type": "ir.actions.act_window_close"}
