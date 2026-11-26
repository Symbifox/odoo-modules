"""Settings panel.

Only scalar field types here (Boolean/Integer/Char/…): Text/Html fields on
res.config.settings crash the whole Settings page. Many2one is fine: the
config parameter stores the id.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def set_values(self):
        icp = self.env["ir.config_parameter"].sudo()
        before = icp.get_param("bf_cx.complaint_ack_days", "2")
        super().set_values()
        after = icp.get_param("bf_cx.complaint_ack_days", "2")
        if before != after:
            # ack_deadline is a stored compute whose parameter is not in its
            # depends: re-flag open complaints so the new delay applies.
            complaints = self.env["bf.cx.complaint"].search(
                [("state", "not in", ("resolved", "closed"))]
            )
            self.env.add_to_compute(
                complaints._fields["ack_deadline"], complaints
            )

    bf_cx_auto_activity = fields.Boolean(
        string="Automatic closed loop",
        config_parameter="bf_cx.auto_activity",
        default=True,
        help="Automatically create a follow-up activity for the owner "
             "when a detractor (NPS ≤ 6) or a dissatisfied rating comes "
             "in.",
    )
    bf_cx_ingest_ratings = fields.Boolean(
        string="Ingest email ratings",
        config_parameter="bf_cx.ingest_ratings",
        default=True,
        help="Copy each rating received (projects, tickets…) into the "
             "unified feedback register.",
    )
    bf_cx_solicitation_cooldown_days = fields.Integer(
        string="Over-solicitation guard (days)",
        config_parameter="bf_cx.solicitation_cooldown_days",
        default=30,
        help="Minimum number of days between two feedback requests to the "
             "same contact (waves, post-meeting, post-loss). 0 = no guard.",
    )
    bf_cx_followup_days = fields.Integer(
        string="Closed-loop deadline (days)",
        config_parameter="bf_cx.followup_days",
        default=2,
        help="Deadline of the activity created for a detractor or a "
             "dissatisfied rating (practice: contact again within 24-72 "
             "h).",
    )
    bf_cx_nps_window_days = fields.Integer(
        string="NPS window (days)",
        config_parameter="bf_cx.nps_window_days",
        default=365,
        help="Rolling window of the NPS score shown (dashboard, digest, "
             "programs). At low volume, 365 days is the only "
             "statistically honest window; the score is hidden below 10 "
             "answers.",
    )
    bf_cx_testimonial_activity = fields.Boolean(
        string="Activity for testimonial candidates",
        config_parameter="bf_cx.testimonial_activity",
        default=True,
        help="Create an activity (5-day deadline) when a respondent "
             "agrees to be quoted - an opt-in is perishable.",
    )
    bf_cx_notify_every_response = fields.Boolean(
        string="Notify each answer",
        config_parameter="bf_cx.notify_every_response",
        default=False,
        help="Send a notice to the owner for EVERY answer, promoters "
             "included. Without it, only detractors are flagged (through "
             "the reminder activity) and an enthusiastic answer goes "
             "unnoticed. Turn it on knowingly: a notification received "
             "for everything ends up no longer being read, including the "
             "day it announces a detractor.",
    )
    bf_cx_test_partner_id = fields.Many2one(
        "res.partner",
        string="Test contact",
        config_parameter="bf_cx.test_partner_id",
        help="Contact who receives the emails of a wave's \"Send a test\" "
             "button. Use an address of your own: the test is sent for "
             "real. It counts in no statistic and does not feed the "
             "register.",
    )
    bf_cx_complaint_ack_days = fields.Integer(
        string="Acknowledgement delay (days)",
        config_parameter="bf_cx.complaint_ack_days",
        default=2,
        help="Number of days to acknowledge a complaint (computes the "
             "deadline shown on each complaint).",
    )
