"""La fusion de contacts fait suivre le titre des pages de liens.

La fusion d'Odoo rattache les pages des fiches qui disparaissent à la fiche
gardée par SQL, sans écrire leur nom : une page gardait le titre de la fiche
fusionnée, et le pont la prenait ensuite pour un titre écrit à la main, qui ne
suit plus jamais.

Ici : les pages dont le titre était le nom de leur fiche, relevées avant la
fusion, prennent après elle le nom de la fiche gardée, sans trace.
"""

from odoo import models


class BasePartnerMergeAutomaticWizard(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        partners = self.env["res.partner"].browse(partner_ids).exists()
        following = self.env["bf.linkpage"]._bf_owner_pages(partners).filtered(
            lambda page: page.name == page.partner_id.name)
        res = super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
        following.invalidate_recordset(["partner_id"])
        following._bf_follow_partner_name()
        return res
