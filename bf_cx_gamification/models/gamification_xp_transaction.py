"""Mirror the CX sources on the XP transaction ledger.

_award_xp() copies the rule source onto the transaction and stores the
awarded record in the Reference field (used by the backfill dedup), so
both closed Selections need the CX entries too.
"""
from odoo import fields, models


class GamificationXpTransaction(models.Model):
    _inherit = "bf.gamification.xp.transaction"

    source = fields.Selection(
        selection_add=[
            ("cx_feedback", "Boucle fermée CX"),
            ("cx_complaint", "Plainte client"),
        ],
        ondelete={
            "cx_feedback": "cascade",
            "cx_complaint": "cascade",
        },
    )
    reference = fields.Reference(
        selection_add=[
            ("bf.cx.feedback", "Feedback CX"),
            ("bf.cx.complaint", "Plainte client"),
        ],
        ondelete={
            "bf.cx.feedback": "set null",
            "bf.cx.complaint": "set null",
        },
    )
