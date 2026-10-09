from odoo import _, models
from odoo.exceptions import UserError

MODEL = "privacy.breach.notice"


class FederationLink(models.Model):
    _inherit = "federation.link"

    def _federation_may_read(self, partners):
        if self.res_model == MODEL:
            return self.env["res.partner"]  # personne n'est admis dans un canal d'avis
        return super()._federation_may_read(partners)

    def action_open_channel(self):
        if self.filtered(lambda l: l.res_model == MODEL):
            raise UserError(_("Un avis de violation n'a pas de canal de discussion : son fil et le "
                              "courriel font foi."))  # même message que discuss_channel.py
        return super().action_open_channel()
