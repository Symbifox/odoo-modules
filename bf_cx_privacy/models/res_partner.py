"""Do-not-contact enforcement.

privacy_consent's DNC list is described as the master switch that overrides
everything - a feedback survey is not marketing, but an explicit "do not
contact me" is an objection that binds every outbound channel. Extending
_bf_cx_split_solicitable() covers waves, post-meeting, post-loss and the
central rating-request hook in one place.
"""
from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _bf_cx_split_solicitable(self, days=None):
        allowed, blocked = super()._bf_cx_split_solicitable(days=days)
        dnc = allowed.filtered("do_not_contact")
        return allowed - dnc, blocked | dnc
