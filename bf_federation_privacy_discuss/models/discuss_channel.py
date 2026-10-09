from odoo import _, api, models
from odoo.exceptions import UserError

MODEL = "privacy.breach.notice"


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    def _breach_channel_refusal(self):
        # Littéral dans `_()` : l'extracteur des traductions ne lit que les littéraux.
        return UserError(_("Un avis de violation n'a pas de canal de discussion : son fil et le "
                           "courriel font foi."))

    def _is_breach_link(self, link_id):
        return bool(link_id) and self.env["federation.link"].sudo().browse(link_id).res_model == MODEL

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(self._is_breach_link(v.get("federation_link_id")) for v in vals_list):
            raise self._breach_channel_refusal()
        channels = super().create(vals_list)
        if not self.env.su and channels.sudo().filtered(lambda c: c.federation_link_id.res_model == MODEL):
            raise self._breach_channel_refusal()  # par un défaut du contexte
        return channels

    def write(self, vals):
        if not self.env.su and self._is_breach_link(vals.get("federation_link_id")):
            raise self._breach_channel_refusal()
        return super().write(vals)
