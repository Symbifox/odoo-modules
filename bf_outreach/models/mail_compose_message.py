# -*- coding: utf-8 -*-
from odoo import models


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    def _action_send_mail_mass_mail(self, res_ids, auto_commit=False):
        """En envoi de masse, Odoo n'écrit pas dans la discussion.

        Le compositeur en mode « mass_mail » fabrique directement des `mail.mail`
        sans passer par `message_post` : la surcharge de `message_post` sur la
        cible ne voit donc rien. On journalise ici, sinon un envoi à cinquante
        cibles n'avancerait aucune cadence.
        """
        mails = super()._action_send_mail_mass_mail(res_ids, auto_commit=auto_commit)
        if self.model == "bf.outreach.target" and res_ids:
            self.env["bf.outreach.target"].browse(res_ids)._log_mass_email_touches(
                self.subject
            )
        return mails
