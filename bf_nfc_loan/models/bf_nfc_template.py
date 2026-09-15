"""La recette « équipements » : un équipement par ligne, et sa pastille de prêt."""
from odoo import fields, models


class BfNfcTemplate(models.Model):
    _inherit = "bf.nfc.template"

    recette = fields.Selection(selection_add=[("equipements", "Des équipements et leur pastille")],
                               ondelete={"equipements": "set default"})
    registre_externe = fields.Boolean(
        string="Remis à des personnes sans compte",
        help="Chaque remise demande le nom, le téléphone et l'employeur de qui reçoit "
             "(registre des cadenas, RSST art. 205).")
    max_days = fields.Integer(string="Rappel après (jours)")

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
