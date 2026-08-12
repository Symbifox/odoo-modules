"""Add the two CX sources to the Fox Quest XP rule engine.

The engine matches rules with a (source, trigger) search performed by
per-model hooks; the source Selection is a closed list, so the bridge
must extend it. 'cascade' is mandatory here: the field is required, so
'set null' is refused at registry setup, and cascading simply drops the
bridge's own noupdate rules on uninstall.
"""
from odoo import fields, models


class GamificationXpRule(models.Model):
    _inherit = "bf.gamification.xp.rule"

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
