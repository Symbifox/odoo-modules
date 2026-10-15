# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, models


class BfEmailAbsence(models.Model):
    _inherit = "bf.email.absence"

    @api.model
    def _calendar_event_arms(self, user, event, pattern):
        # Une occurrence d'une série d'un agenda abonné n'arme rien, comme une
        # occurrence de récurrence d'Odoo.
        if getattr(event, "bf_subscription_serie", False):
            return False
        return super()._calendar_event_arms(user, event, pattern)
