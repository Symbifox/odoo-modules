"""Le compositeur, en commentaire comme en envoi de masse, exige l'écriture sur l'avis.

En envoi de masse, le compositeur crée les courriels en sudo dès que les fiches sont lisibles,
sans passer par le contrôle du gabarit : un lecteur faisait partir le courriel officiel de
l'avis, PDF signé joint, chez qui il voulait. Le contrôle porte sur les fiches résolues (par
identifiants comme par domaine), là où le cœur les envoie.
"""
from odoo import models

WATCHED = "privacy.breach.notice"


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    def _breach_check(self, res_ids):
        if self.model == WATCHED:
            self.env["mail.message"]._breach_check_register(res_ids)

    def _action_send_mail_comment(self, res_ids):
        self._breach_check(res_ids)
        return super()._action_send_mail_comment(res_ids)

    def _action_send_mail_mass_mail(self, res_ids, auto_commit=False):
        self._breach_check(res_ids)
        return super()._action_send_mail_mass_mail(res_ids, auto_commit=auto_commit)
