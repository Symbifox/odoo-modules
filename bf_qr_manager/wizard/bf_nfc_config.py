from odoo import api, fields, models

from ..hooks import TYPES_SUGGERES
from odoo.addons.bf_nfc.hooks import semer_les_types
from odoo.addons.bf_nfc.wizard.bf_nfc_config import _exiger_la_gestion


class BfNfcConfig(models.TransientModel):
    _inherit = "bf.nfc.config"

    qr_groupe_ids = fields.Many2many(
        "res.groups", "bf_qr_config_group_rel", "config_id", "group_id",
        string="Qui associe les étiquettes QR",
        help="En plus de la gestion des pastilles, qui le peut toujours. Vaut pour la "
             "société courante.")

    @api.model
    def default_get(self, champs):
        valeurs = super().default_get(champs)
        valeurs["qr_groupe_ids"] = [(6, 0, self.env.company.sudo().bf_qr_groupe_ids.ids)]
        return valeurs

    def action_enregistrer(self):
        _exiger_la_gestion(self.env)
        self.env.company.sudo().bf_qr_groupe_ids = [(6, 0, self.qr_groupe_ids.ids)]
        return super().action_enregistrer()

    def action_types_suggeres(self):
        _exiger_la_gestion(self.env)
        semer_les_types(self.env, TYPES_SUGGERES)
        return self.action_types_de_fiche()
