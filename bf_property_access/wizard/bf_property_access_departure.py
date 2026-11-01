from odoo import _, fields, models
from odoo.exceptions import AccessError


class BfPropertyAccessDeparture(models.TransientModel):
    _name = "bf.property.access.departure"
    _description = "Confirmer le départ d'un résident"

    resident_ids = fields.Many2many(
        "bf.property.access.resident", string="Résidents", required=True
    )
    date_keys = fields.Date(
        string="Clés remises le",
        required=True,
        default=fields.Date.context_today,
        help="Le jour où la personne a rendu les clés ou quitté les lieux, "
             "constaté. Pas une date prévue.",
    )

    def action_confirm(self):
        if not self.env.user.has_group("bf_property_core.group_bf_property_manager"):
            raise AccessError(_("Réservé aux gestionnaires."))
        self.resident_ids._bf_confirm_departure(self.date_keys)
        return {"type": "ir.actions.act_window_close"}
