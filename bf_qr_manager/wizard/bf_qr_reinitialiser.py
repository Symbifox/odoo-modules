"""Réinitialiser plusieurs étiquettes : une seule confirmation pour tout le lot.

Sur la fiche, chaque étiquette se confirme à part. Ici, l'écran dit combien
d'étiquettes vont redevenir vierges et combien le sont déjà, puis un seul bouton
agit.
"""
from odoo import _, api, fields, models


class BfQrReinitialiser(models.TransientModel):
    _name = "bf.qr.reinitialiser"
    _description = "Réinitialiser des étiquettes QR"

    tag_ids = fields.Many2many("bf.nfc.tag", string="Étiquettes", readonly=True)
    count_a_vider = fields.Integer(string="À réinitialiser", compute="_compute_counts")
    count_deja = fields.Integer(string="Déjà vierges", compute="_compute_counts")

    @api.depends("tag_ids")
    def _compute_counts(self):
        for assistant in self:
            etiquettes = assistant.tag_ids.sudo()
            assistant.count_deja = len(etiquettes.filtered("qr_vierge"))
            assistant.count_a_vider = len(etiquettes) - assistant.count_deja

    def action_reinitialiser(self):
        self.ensure_one()
        nombre = self.count_a_vider
        self.tag_ids._reinitialiser()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "type": "success",
            "message": _("%s étiquettes réinitialisées.", nombre),
            "next": {"type": "ir.actions.act_window_close"}}}
