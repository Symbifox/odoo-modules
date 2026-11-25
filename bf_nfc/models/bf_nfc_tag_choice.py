"""Les choix d'une pastille « menu » : plusieurs gestes sur une seule puce.

Une pastille sur un portable : « Je le prends », « Je le rapporte », « Signaler
un problème ». Une pastille sur une porte : « J'arrive », « Je pars ».

⚠️ Tous les choix visent la fiche de la pastille. Un choix dont le geste exige un
autre type de fiche est refusé au tapotement, avec une phrase, plutôt que
d'agir sur la mauvaise fiche.

⚠️ Pas de menu dans un menu : ce serait une arborescence que personne ne
parcourt debout, le téléphone à la main.
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class BfNfcTagChoice(models.Model):
    _name = "bf.nfc.tag.choice"
    _description = "Menu tag choice"
    _order = "sequence, id"

    tag_id = fields.Many2one("bf.nfc.tag", string="Tag", required=True,
                             ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Button label", required=True)
    gesture_id = fields.Many2one("bf.nfc.gesture", string="Gesture", required=True,
                                 ondelete="restrict", domain=[("kind", "!=", "menu")])
    params = fields.Char(string="Parameters", help="Optional JSON added "
                                                   "to the tag's own.")
    style = fields.Selection(
        [("principal", "Primary"), ("secondaire", "Secondary"), ("danger", "Warning")],
        default="secondaire", required=True,
    )

    @api.constrains("gesture_id")
    def _check_pas_de_menu(self):
        for choix in self:
            if choix.gesture_id.kind == "menu":
                raise ValidationError(_("A choice cannot open another menu."))

    def _params(self):
        self.ensure_one()
        if not self.params:
            return {}
        try:
            charge = json.loads(self.params)
        except ValueError:
            raise UserError(_("The parameters of the choice “%s” are not "
                              "valid JSON.", self.name))
        if not isinstance(charge, dict):
            raise UserError(_("A choice's parameters must be a JSON object."))
        return charge
