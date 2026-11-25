"""La recette « équipements » : un équipement par ligne, et sa pastille de prêt."""
from odoo import fields, models


class BfNfcTemplate(models.Model):
    _inherit = "bf.nfc.template"

    recette = fields.Selection(selection_add=[("equipements", "Equipment "
                                                              "items and "
                                                              "their tags")],
                               ondelete={"equipements": "set default"})
    registre_externe = fields.Boolean(
        string="Handed out to people without an account",
        help="Each hand-out asks for the name, phone number and employer "
             "of the recipient (padlock register, RSST s. 205).")
    max_days = fields.Integer(string="Reminder after (days)")

    def _recette_equipements(self, pose, lignes):
        self.ensure_one()
        geste = self.env.ref("bf_nfc_loan.gesture_loan")
        Tag = self.env["bf.nfc.tag"]
        crees = Tag.browse()
        for nom, endroit in lignes:
            equipement = self.env["bf.nfc.equipment"].create({
                "name": nom, "place": endroit or False,
                "registre_externe": self.registre_externe, "max_days": self.max_days,
            })
            crees |= Tag.create({
                "name": ("%s · %s" % (pose.prefixe.strip(), nom)) if pose.prefixe else nom,
                "place": endroit or False, "gesture_id": geste.id,
                "res_model": equipement._name, "res_id": equipement.id,
            })
        return crees
