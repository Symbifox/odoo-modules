"""La salle, et ce que l'agenda doit savoir d'elle.

⚠️ Une réservation est un `calendar.event` ordinaire qui porte une salle : pas de
modèle parallèle. Ce qu'on réserve à la porte se voit dans l'agenda de tout le
monde, et ce qu'on réserve dans l'agenda se voit à la porte.
"""
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfNfcRoom(models.Model):
    _name = "bf.nfc.room"
    _description = "Room bookable by tag"
    _inherit = ["bf.nfc.target.mixin"]
    _order = "name"

    name = fields.Char(string="Room", required=True)
    place = fields.Char(string="Where", help="Floor, wing: what you say to "
                                          "send someone there.")
    capacity = fields.Integer(string="Seats")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    checkin_delay = fields.Integer(
        string="Confirmation delay (min)", default=10,
        help="A booking nobody confirms with the tag within this delay "
             "after it starts loses its room.")
    require_checkin = fields.Boolean(
        string="Release unconfirmed bookings", default=True,
        help="Unchecked: a booking keeps its room even if nobody shows up.")
    durations = fields.Char(
        string="Offered durations (min)", default="30,60",
        help="The door's “Take” buttons, in minutes, separated by commas.")
    event_ids = fields.One2many("calendar.event", "bf_nfc_room_id", string="Bookings")
    note = fields.Text()

    def _durees(self):
        self.ensure_one()
        valeurs = []
        for morceau in (self.durations or "").split(","):
            try:
                minutes = int(morceau.strip())
            except ValueError:
                continue
            if 5 <= minutes <= 8 * 60:
                valeurs.append(minutes)
        return sorted(set(valeurs)) or [30, 60]

    @api.model
    def _cron_liberer(self):
        """Rend la salle des réservations que personne n'a confirmées à temps.

        ⚠️ L'événement n'est PAS supprimé : il appartient à son organisateur, qui
        a peut-être simplement déplacé sa rencontre. Seule la salle se libère,
        et une note le dit sur l'événement (une note : aucun courriel).
        """
        maintenant = fields.Datetime.now()
        Evenement = self.env["calendar.event"].sudo()
        for salle in self.sudo().search([("require_checkin", "=", True)]):
            limite = maintenant - timedelta(minutes=salle.checkin_delay or 10)
            orphelins = Evenement.search([
                ("bf_nfc_room_id", "=", salle.id), ("bf_nfc_checkin", "=", False),
                ("start", "<=", limite), ("stop", ">", maintenant), ("allday", "=", False),
            ])
            for evenement in orphelins:
                evenement.bf_nfc_room_id = False
                evenement.message_post(
                    body=Markup("<p>%s</p>") % escape(_(
                        "Room “%(salle)s” released: nobody confirmed "
                        "their presence with the tag within %(delai)s "
                        "minutes.", salle=salle.name,
                        delai=salle.checkin_delay or 10)),
                    message_type="comment", subtype_xmlid="mail.mt_note")


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    bf_nfc_room_id = fields.Many2one("bf.nfc.room", string="Room", index=True, tracking=True)
    bf_nfc_checkin = fields.Datetime(string="Confirmed at the door", copy=False)

    @api.constrains("bf_nfc_room_id", "start", "stop")
    def _check_salle_libre(self):
        for evenement in self.filtered("bf_nfc_room_id"):
            conflit = self.sudo().search([
                ("id", "!=", evenement.id),
                ("bf_nfc_room_id", "=", evenement.bf_nfc_room_id.id),
                ("start", "<", evenement.stop), ("stop", ">", evenement.start),
            ], limit=1)
            if conflit:
                raise ValidationError(_(
                    "The room “%(salle)s” is already booked from "
                    "%(debut)s to %(fin)s.",
                    salle=evenement.bf_nfc_room_id.name,
                    debut=fields.Datetime.context_timestamp(self, conflit.start).strftime("%H:%M"),
                    fin=fields.Datetime.context_timestamp(self, conflit.stop).strftime("%H:%M")))
