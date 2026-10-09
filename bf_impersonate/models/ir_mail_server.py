from odoo import _, models
from odoo.exceptions import UserError

from .. import impersonation as imp


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    def send_email(self, message, *args, **kwargs):
        # Un envoi direct, sans passer par mail.mail, sortirait sous la
        # personne : refusé comme la création d'un mail.mail.
        if imp.current():
            raise UserError(_(
                "Nothing is sent while you see Symbifox as someone else. Go back "
                "to your own account to send it."))
        return super().send_email(message, *args, **kwargs)
