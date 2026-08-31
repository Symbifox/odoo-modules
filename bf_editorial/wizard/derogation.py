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
    _description = "Signature d'une dérogation éditoriale"

    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entrée", required=True,
        ondelete="cascade", readonly=True,
    )
    problems = fields.Text(
        string="Ce que vous signez", readonly=True,
        help="Les motifs de refus que cette dérogation couvrira, tels qu'ils"
             " se lisent maintenant. Ils sont figés à la signature : un motif"
             " qui change de texte ensuite n'est plus couvert.",
    )
    reason = fields.Text(
        string="Raison", required=True,
        help="Pourquoi ces motifs sont acceptables sur cet article-là.",
    )

    def action_sign(self):
        self.ensure_one()
        raison = (self.reason or "").strip()
        if not raison:
            raise UserError(_(
                "Une dérogation sans raison écrite ne se relit pas. Dites"
                " pourquoi ces motifs sont acceptables ici."
            ))
        self.entry_id._sign_waiver(raison)
        return {"type": "ir.actions.act_window_close"}
