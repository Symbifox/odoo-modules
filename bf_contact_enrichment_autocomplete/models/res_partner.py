"""Le clic sur une suggestion sans crédit.

La recherche par nom d'Odoo (D&B) est gratuite ; l'enrichissement qui suit le
clic coûte un crédit IAP. Sans crédit, le widget fusionne la suggestion (nom,
ville, pays) dans la fiche puis affiche « Not enough credits » avec un lien
d'achat. Il affiche tel quel tout autre ``error_message`` : on remplace donc le
message, pas le comportement. La suggestion entre toujours dans la fiche, et
c'est la création de la société qui la met dans la file de Gen
(``bf_contact_enrichment``).

Seulement pour qui verra réellement Gen passer : un membre du groupe
d'enrichissement dont la préférence n'est pas « ne rien lancer ». Les autres
gardent l'invitation d'Odoo, qui reste vraie pour eux.
"""
from odoo import _, api, models

_SANS_CREDIT = "Insufficient Credit"


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def _bf_gen_relayer_sans_credit(self, result):
        if not (isinstance(result, dict) and result.get("error")
                and result.get("error_message") == _SANS_CREDIT):
            return result
        user = self.env.user
        if user.bf_gen_company_mode == "off" or not self._bf_gen_peut_demander(user):
            return result
        return dict(result, error_message=_(
            "Gen will complete this company with an online search after "
            "you save it."))

    @api.model
    def enrich_by_duns(self, duns, timeout=15):
        return self._bf_gen_relayer_sans_credit(
            super().enrich_by_duns(duns, timeout=timeout))

    @api.model
    def enrich_by_gst(self, gst, timeout=15):
        return self._bf_gen_relayer_sans_credit(
            super().enrich_by_gst(gst, timeout=timeout))
