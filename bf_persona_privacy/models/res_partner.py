"""Un contact supprimé emporte son persona par l'ORM, pas par la cascade SQL.

`contact.persona.partner_id` est `ondelete="cascade"` : supprimer le contact
efface le persona au niveau SQL, sans que l'ORM n'appelle jamais
`contact.persona.unlink()`. Le fil du persona (`mail.message` rattachés par
modèle et numéro, dont l'historique suivi de la qualité de payeur), ses abonnés
et ses pièces jointes seraient restés orphelins.
"""
from odoo import models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def unlink(self):
        personas = self.env["contact.persona"].sudo().with_context(active_test=False).search(
            [("partner_id", "in", self.ids)])
        if personas:
            personas.unlink()
        return super().unlink()
