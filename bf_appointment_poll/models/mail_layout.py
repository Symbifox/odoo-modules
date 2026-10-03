"""La société qui habille les courriels du sondage.

Un participant n'a pas de `company_id` : sans ce qui suit, la mise en page commune
prendrait la société de l'utilisateur qui envoie, et l'en-tête pourrait nommer une
autre société que le contenu, qui suit celle du type de rendez-vous du sondage.
"""
from odoo import models


class AppointmentPollParticipant(models.Model):
    _inherit = "appointment.poll.participant"

    def _mail_get_companies(self, default=False):
        defaut = default or self.env["res.company"]
        return {
            participant.id: (participant.poll_id.type_id.company_id
                             or participant.poll_id.company_id or defaut)
            for participant in self
        }
