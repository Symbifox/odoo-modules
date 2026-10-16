# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Un événement d'un agenda abonné est en lecture seule (lot 6).

Il appartient à l'autre agenda : le modifier ici ne changerait rien là-bas, et
la lecture suivante le remettrait. Seule la synchronisation l'écrit.
"""

from odoo import _, fields, models
from odoo.exceptions import UserError

CHAMPS_VERROUILLES = {
    "name", "start", "stop", "allday", "start_date", "stop_date", "duration", "location",
    "description", "privacy", "show_as", "rrule", "recurrency", "partner_ids", "user_id",
    "bf_subscription_id", "bf_subscription_key", "bf_subscription_serie", "x_nc_calendar_id",
    # Relecture adverse : ces champs-ci faisaient partir l'événement vers Nextcloud
    # ou le faisaient passer pour une plage de rencontre.
    "categ_ids", "bf_block_type_id", "videocall_location", "bf_event_status", "attendee_ids",
}


class CalendarEvent(models.Model):
    # ⚠️ Le mixin est déclaré ici, pas emprunté : la règle de couleur des agendas
    # abonnés (data/bf_color_rule.xml) exige un calendar.event coloriable. Un
    # autre module peut déclarer le même mixin, la double déclaration est sans
    # effet ; sans aucun, l'installation s'arrêtait sur « Calendar Event has no
    # free color ».
    _name = "calendar.event"
    _inherit = ["calendar.event", "bf.color.mixin"]

    bf_subscription_id = fields.Many2one(
        "bf.calendar.subscription", string="External calendar", index=True,
        ondelete="cascade", readonly=True, copy=False)
    bf_subscription_key = fields.Char(index=True, readonly=True, copy=False)
    # Occurrence d'une série de l'autre agenda : la série est dépliée à
    # l'import, et ce marqueur garde ce que `recurrency` ne dit plus (un férié
    # répété n'est pas une absence, bf_email_management).
    bf_subscription_serie = fields.Boolean(readonly=True, copy=False)

    def _bf_abonnement_verrouille(self):
        # Seule la synchronisation écrit, et elle tourne en sudo. Pas de clé de
        # contexte : celui d'un appel RPC appartient à l'appelant.
        return not self.env.su and any(self.sudo().mapped("bf_subscription_id"))

    def write(self, vals):
        if CHAMPS_VERROUILLES & set(vals) and self._bf_abonnement_verrouille():
            raise UserError(_("This event comes from a subscribed external calendar: "
                              "change it in that calendar."))
        return super().write(vals)

    def unlink(self):
        if self._bf_abonnement_verrouille():
            raise UserError(_("This event comes from a subscribed external calendar: "
                              "it cannot be deleted here."))
        return super().unlink()
