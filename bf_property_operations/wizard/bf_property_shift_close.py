"""Fermer un quart, c'est nommer qui reprend et dire ce qui ne se lit pas.

⚠️ L'assistant n'existe pas pour faire joli. `action_close` refuse une
fermeture qui laisse du travail sans destinataire, et refuse une passation
muette ; sans écran pour poser les deux, la personne se heurterait au refus
sans avoir eu l'endroit où y répondre. Un refus dont on ne peut pas sortir est
un défaut, pas une garde.
"""
from odoo import _, api, fields, models


class BfPropertyShiftCloseWizard(models.TransientModel):
    _name = "bf.property.shift.close.wizard"
    _description = "Fermeture d'un quart et passation"

    shift_id = fields.Many2one(
        "bf.property.shift", string="Quart", required=True, readonly=True
    )
    carried_count = fields.Integer(
        string="Travaux non réglés", compute="_compute_carried", readonly=True
    )
    # ⚠️ Le domaine du sélecteur en a besoin, et un domaine ne traverse pas un
    # point : sans ce champ lié, l'écran proposait les quarts de TOUTES les
    # équipes. Il n'est pas affiché — il sert au domaine.
    maintenance_team_id = fields.Many2one(
        related="shift_id.maintenance_team_id", string="Équipe", readonly=True
    )
    next_shift_id = fields.Many2one(
        "bf.property.shift",
        string="Repris par le quart",
        help="Le quart qui prend en charge ce qui reste. Il doit être ouvert "
             "ou préparé : on ne passe pas de travail à un quart déjà fermé.",
    )
    handover_note = fields.Text(
        string="Ce qui passe au quart suivant",
        help="Ce que le quart suivant doit savoir et que les enregistrements "
             "ne disent pas.",
    )

    @api.depends("shift_id")
    def _compute_carried(self):
        for wizard in self:
            wizard.carried_count = len(wizard.shift_id._carried_lines())

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        shift = self.env["bf.property.shift"].browse(
            values.get("shift_id") or self.env.context.get("default_shift_id")
        )
        if shift and "handover_note" in fields_list:
            values.setdefault("handover_note", shift.handover_note)
        if shift and "next_shift_id" in fields_list:
            # Le quart suivant le plus plausible : même équipe, commence après
            # celui-ci, pas encore fermé. Proposé, jamais imposé.
            following = self.env["bf.property.shift"].search(
                [
                    ("maintenance_team_id", "=", shift.maintenance_team_id.id),
                    ("date_start", ">=", shift.date_start),
                    ("id", "!=", shift.id),
                    ("state", "!=", "closed"),
                ],
                order="date_start asc",
                limit=1,
            )
            if following:
                values.setdefault("next_shift_id", following.id)
        return values

    def action_confirm(self):
        self.ensure_one()
        if self.handover_note is not False:
            self.shift_id.handover_note = self.handover_note
        self.shift_id.action_close(next_shift=self.next_shift_id or None)
        return {"type": "ir.actions.act_window_close"}
