from odoo import api, models

from odoo.addons.bf_preferred_name.models.res_partner import LEGAL_NAME_GROUP


class BfSignField(models.Model):
    _inherit = "bf.sign.field"

    def _auto_value(self):
        # Le pavé « nom » estampé sur le PDF est l'acte : le nom légal figé.
        self.ensure_one()
        if self.field_type == "name" and self.signer_id:
            return self.signer_id._bf_act_name()
        return super()._auto_value()

    # Les dépendances du parent, répétées : décorer la surcharge sans elles
    # risquait de les remplacer.
    @api.depends("field_type", "fill_mode", "value_text", "filled_value",
                 "copy_from_id", "signer_id.state", "signer_id.name")
    @api.depends_context("uid")
    def _compute_value_view(self):
        # La colonne « Valeur » d'une demande passe par `_auto_value` : hors du
        # groupe « Noms légaux », elle livrait le nom légal en clair (lecture,
        # API, export). Elle y montre le nom
        # du signataire ; l'estampage, qui appelle `_display_value`, garde
        # l'acte. Le test porte sur l'usager réel, jamais sur une clé de
        # contexte, qu'un appel à l'API poserait.
        super()._compute_value_view()
        if self.env.user.has_group(LEGAL_NAME_GROUP):
            return
        for rec in self:
            origin = rec.copy_from_id if rec.fill_mode == "copy" else rec
            if origin.field_type == "name" and origin.fill_mode == "auto" and origin.signer_id:
                rec.value_display = origin.signer_id.name or ""
