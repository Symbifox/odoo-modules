"""Qui reçoit un rappel de confirmation la veille, et ce qu'il dit de plus.

L'armement vit sur l'ÉTIQUETTE de rencontre, pas sur un interrupteur global,
par la même règle que l'avis de changement automatique juste à côté : un module
qui se met à écrire aux invités le jour de sa pose n'a pas de premier jour sûr.
Tant qu'aucune étiquette n'est armée, le cron tourne et n'envoie rien.

Pourquoi l'étiquette plutôt que le type de rendez-vous : la demande vient de
visites sur place, qui sont des rencontres créées à la main dans l'agenda et non
des réservations prises par un lien (celles-là ont déjà leur rappel, dans
`bf_appointment`). Une rencontre créée à la main n'a pas de type ; elle a des
étiquettes, et la bulle de création rapide les offre déjà.

La ligne propre au type existe parce que la consigne n'est pas la même partout :
une visite sur place peut demander de préparer un accès à Internet, et servir
cette phrase aux invités de toutes les rencontres la rendrait absurde.
"""

from odoo import fields, models


class CalendarEventType(models.Model):
    _inherit = "calendar.event.type"

    bf_confirm_reminder = fields.Boolean(
        string="Day-before confirmation reminder",
        help="Meetings carrying this tag get a reminder the day before, asking "
             "the guest to confirm: the hour, the line below, and the link "
             "where they confirm or cancel themselves when the meeting has a "
             "single outside guest.",
    )
    bf_confirm_reminder_from_id = fields.Many2one(
        "res.users",
        string="Reminder sent from",
        domain="[('share', '=', False)]",
        help="Whose address the reminder leaves from. Needed whenever the "
             "meetings come from a calendar sync, which leaves OdooBot as the "
             "organiser: without it the reminder has nobody to sign for and is "
             "not sent at all. Left empty, the organiser is used, then any "
             "internal attendee.",
    )
    bf_confirm_reminder_note = fields.Text(
        string="Line to add to the reminder",
        help="Sentence of this kind of meeting, added to the reminder: what to "
             "get ready, for instance. Left empty, the reminder carries only "
             "the hour and the link.",
    )
