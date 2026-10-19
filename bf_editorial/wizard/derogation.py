# -*- coding: utf-8 -*-
"""La fenêtre de signature d'une dérogation.

Une case à cocher aurait suffi techniquement. Elle aurait aussi permis de
lever la garde sans lire ce qu'on lève, et une dérogation qu'on signe sans
regarder n'est pas une décision, c'est un réflexe.

La fenêtre montre donc les motifs exacts qu'elle va couvrir, en lecture seule,
et refuse de se fermer sans une raison écrite. La raison part au chatter avec
le nom de qui signe : c'est elle qu'on relira dans six mois pour savoir
pourquoi cet article est sorti court, ou avec deux tirets cadratins dedans.
"""

from odoo import _, fields, models
from odoo.exceptions import UserError


class EditorialWaiver(models.TransientModel):
    _name = "bf.editorial.waiver"
    _description = "Editorial waiver signature"

    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entry", required=True,
        ondelete="cascade", readonly=True,
    )
    problems = fields.Text(
        string="What you are signing", readonly=True,
        help="The refusal reasons this waiver will cover, as they read "
             "now. They are frozen at signing: a reason whose text "
             "changes afterwards is no longer covered.",
    )
    reason = fields.Text(
        string="Reason", required=True,
        help="Why these reasons are acceptable on this particular article.",
    )

    def action_sign(self):
        self.ensure_one()
        raison = (self.reason or "").strip()
        if not raison:
            raise UserError(_(
                "A waiver without a written reason cannot be reviewed "
                "later. Say why these reasons are acceptable here."
            ))
        self.entry_id._sign_waiver(raison)
        return {"type": "ir.actions.act_window_close"}
