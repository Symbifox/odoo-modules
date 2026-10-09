"""Un gabarit sur l'avis ne s'envoie pas avec la seule lecture, et le gabarit officiel ne se
change qu'en administration.

`mail.template.send_mail` ne demande que la lecture de la fiche : un lecteur pouvait faire
partir le courriel officiel de l'avis, au nom de la société et PDF signé joint, vers une adresse
de son choix, ou une copie du gabarit faite pour l'occasion. Là où `mail.restrict.template.rendering`
est faux (défaut d'Odoo), tout employé peut aussi éditer les gabarits : le corps, le Reply-To ou
la suppression du gabarit officiel détourneraient l'avis.
"""
from odoo import _, models
from odoo.exceptions import UserError

WATCHED = "privacy.breach.notice"
XMLID = "privacy_breach_notice.mail_template_breach_notice"


class MailTemplate(models.Model):
    _inherit = "mail.template"

    def _send_check_access(self, res_ids):
        super()._send_check_access(res_ids)
        if self.model == WATCHED:
            self.env["mail.message"]._breach_check_register(res_ids)

    def _breach_guard_official(self):
        ours = self.env.ref(XMLID, raise_if_not_found=False)
        if ours and ours in self and not self.env["mail.message"]._breach_register_actor_is_system():
            raise UserError(_("Le gabarit officiel de l'avis de violation ne se modifie qu'en administration."))

    def write(self, vals):
        self._breach_guard_official()
        return super().write(vals)

    def unlink(self):
        self._breach_guard_official()
        return super().unlink()
