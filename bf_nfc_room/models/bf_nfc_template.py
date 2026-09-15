"""La recette « salles » : une salle par ligne, et la pastille de sa porte."""
from odoo import fields, models


class BfNfcTemplate(models.Model):
    _inherit = "bf.nfc.template"

    recette = fields.Selection(selection_add=[("salles", "Des salles et la pastille de leur porte")],
                               ondelete={"salles": "set default"})

    def _recette_salles(self, pose, lignes):
        self.ensure_one()
        geste = self.env.ref("bf_nfc_room.gesture_room")
        Tag = self.env["bf.nfc.tag"]
        crees = Tag.browse()
        for nom, endroit in lignes:
            salle = self.env["bf.nfc.room"].create({"name": nom, "place": endroit or False})
            crees |= Tag.create({
                "name": ("%s · %s" % (pose.prefixe.strip(), nom)) if pose.prefixe else nom,
                "place": endroit or False, "gesture_id": geste.id,
                "res_model": salle._name, "res_id": salle.id,
            })
        return crees
