from odoo import _, api, models
from odoo.exceptions import UserError

WATCHED = "privacy.breach.notice"


class SmsSms(models.Model):
    _inherit = "sms.sms"

    def _breach_refuse(self):
        if self.sudo().mail_message_id.filtered(lambda m: m.model == WATCHED and m.res_id) \
                and not self.env["mail.message"]._breach_register_actor_is_system():
            raise UserError(_("Un avis de violation ne part pas par SMS : le courriel à l'adresse "
                              "désignée fait foi."))

    @api.model_create_multi
    def create(self, vals_list):
        sms = super().create(vals_list)
        sms._breach_refuse()
        return sms

    def write(self, vals):
        res = super().write(vals)
        if "mail_message_id" in vals:
            self._breach_refuse()
        return res
