"""La fusion de contacts supprime le persona de trop par l'ORM.

`bf_persona` impose un persona par contact (`unique(partner_id)`). Quand deux
fiches fusionnées en ont un, la mise à jour des clés étrangères d'Odoo SUPPRIME
le persona source en SQL : son fil (dont l'historique de la qualité de payeur),
ses abonnés, ses activités, ses pièces jointes et celles de ses indicateurs
restaient orphelins. On garde celui de la fiche de destination (à défaut, le
premier), et les autres partent par `contact.persona.unlink()` avant la fusion.
"""
from odoo import models


class MergePartnerAutomatic(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _update_foreign_keys(self, src_partners, dst_partner):
        Persona = self.env["contact.persona"].sudo().with_context(active_test=False)
        personas = Persona.search([("partner_id", "in", (src_partners | dst_partner).ids)])
        if len(personas) > 1:
            keep = personas.filtered(lambda p: p.partner_id == dst_partner)[:1] or personas[:1]
            (personas - keep).unlink()
        return super()._update_foreign_keys(src_partners, dst_partner)
