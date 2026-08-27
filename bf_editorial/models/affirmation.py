# -*- coding: utf-8 -*-
"""Les affirmations vérifiables d'un article, et leur verdict.

Un verdict porte toujours son auteur. Un verdict proposé par une machine reste
« proposé » tant qu'un humain ne l'a pas confirmé : c'est la seule façon de ne
pas reproduire une passe de QA déclarée propre à tort.
"""

from odoo import _, api, fields, models


class EditorialClaim(models.Model):
    _name = "bf.editorial.claim"
    _description = "Affirmation vérifiable"
    _order = "sequence, id"

    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entrée", required=True,
        ondelete="cascade", index=True,
    )
    sequence = fields.Integer(string="Séquence", default=10)
    name = fields.Text(string="Affirmation", required=True)
    source_id = fields.Many2one(
        "bf.editorial.source", string="Source",
        domain="[('entry_id', '=', entry_id)]",
    )
    verdict = fields.Selection(
        [
            ("todo", "À vérifier"),
            ("ok", "Exacte"),
            ("imprecise", "Imprécise"),
            ("false", "Fausse"),
        ],
        string="Verdict", default="todo", required=True,
    )
    correction = fields.Text(string="Correction appliquée")
    verdict_by = fields.Many2one("res.users", string="Verdict par")
    verdict_date = fields.Date(string="Verdict du")
    is_machine_proposed = fields.Boolean(
        string="Proposé par la machine", readonly=True,
        help="Un verdict proposé automatiquement n'est pas un verdict rendu :"
             " il attend une confirmation humaine.",
    )
    confirmed = fields.Boolean(
        string="Confirmé", compute="_compute_confirmed", store=True,
    )

    @api.depends("verdict", "is_machine_proposed")
    def _compute_confirmed(self):
        for claim in self:
            claim.confirmed = bool(
                claim.verdict != "todo" and not claim.is_machine_proposed
            )

    def action_confirm(self):
        """Un humain endosse le verdict proposé."""
        self.write({
            "is_machine_proposed": False,
            "verdict_by": self.env.uid,
            "verdict_date": fields.Date.context_today(self),
        })
        return True
