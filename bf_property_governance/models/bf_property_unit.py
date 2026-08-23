"""Ce que l'assemblée a besoin de savoir de la fraction.

Art. 1076 C.c.Q. : « Le syndicat peut, s'il y est autorisé, acquérir ou aliéner
des fractions […]. L'acquisition qu'il fait d'une fraction n'enlève pas son
caractère à la partie privative. Cependant, en assemblée générale, il ne dispose
d'aucune voix pour ces parties et le total des voix qui peuvent être exprimées
est réduit d'autant. »

La règle se lit au **registre**, pas à la feuille de présence. Une fraction que
le syndicat détient sort du total des voix qu'une ligne de présence ait été
chargée ou non — et c'est ce qui la distingue de la privation de l'art. 1094,
qui naît d'un fait extérieur, des charges impayées, porté à la main sur une
ligne. Un retranchement qui n'existerait que sur la feuille de présence
disparaîtrait au premier chargement incomplet, sans que rien ne le signale.
"""
from odoo import api, fields, models
from odoo.tools import float_round

VOTE_DIGITS = 4


class BfPropertyUnit(models.Model):
    _inherit = "bf.property.unit"

    syndicat_held_votes = fields.Float(
        string="Voix retirées (art. 1076)",
        compute="_compute_syndicat_held_votes",
        store=True,
        digits=(16, VOTE_DIGITS),
        help="Art. 1076 C.c.Q. : part de la quote-part que le syndicat détient "
             "lui-même. Ces voix ne s'exercent pas en assemblée et viennent en "
             "diminution du total des voix qui peuvent être exprimées.",
    )

    @api.depends(
        "quote_part",
        "ownership_ids.partner_id",
        "ownership_ids.share",
        "ownership_ids.is_current",
        "syndicat_id.partner_id",
    )
    def _compute_syndicat_held_votes(self):
        for unit in self:
            partner = unit.syndicat_id.partner_id
            if not partner:
                # Sans fiche de personne morale au syndicat, rien ne distingue
                # le syndicat d'un copropriétaire ordinaire. On ne devine pas :
                # une acquisition par le syndicat se constate au titre, et se
                # saisit au registre comme n'importe quelle autre.
                unit.syndicat_held_votes = 0.0
                continue
            share = sum(
                unit.ownership_ids.filtered(
                    lambda o, p=partner: o.is_current and o.partner_id == p
                ).mapped("share")
            )
            unit.syndicat_held_votes = float_round(
                unit.quote_part * share / 100.0, precision_digits=VOTE_DIGITS
            )
