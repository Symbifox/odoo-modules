import re

from odoo import _, models
from odoo.exceptions import UserError

# Texte à remplacer du gabarit « Mise à jour au client » (FR et EN). Laissé tel
# quel, il partait chez le client.
PLACEHOLDER = re.compile(
    r"\[(Décrivez ici l(?:'|&#39;|&#x27;|’)avancement|Describe the progress)")


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    def _action_send_mail(self, auto_commit=False):
        for wizard in self:
            if wizard.model == "helpdesk.ticket" and PLACEHOLDER.search(str(wizard.body or "")):
                raise UserError(_(
                    "Le message contient encore le texte à remplacer du gabarit "
                    "(« [Décrivez ici… »). Écrivez la mise à jour avant d'envoyer."))
        return super()._action_send_mail(auto_commit=auto_commit)
