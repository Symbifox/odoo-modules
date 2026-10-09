"""Un avis de violation ne relaie aucun message, dans aucun sens.

Son fil est un registre de preuve, pas une conversation : l'avis, ses versions et l'accusé
voyagent par leurs propres genres. Relayer les messages laissait le client écrire au fil de
preuve du mandataire, et l'inverse.
"""
from odoo import models

MODEL = "privacy.breach.notice"


class MailMessage(models.Model):
    _inherit = "mail.message"

    def _federation_forward(self):
        return super(MailMessage, self.filtered(lambda m: m.model != MODEL))._federation_forward()
