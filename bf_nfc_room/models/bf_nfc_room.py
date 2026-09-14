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
    _description = "Salle réservable par pastille"
    _order = "name"

    name = fields.Char(string="Salle", required=True)
    place = fields.Char(string="Où", help="Étage, aile : ce qu'on dit pour y envoyer quelqu'un.")
    capacity = fields.Integer(string="Places")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    checkin_delay = fields.Integer(
        string="Délai de confirmation (min)", default=10,
        help="Une réservation que personne ne confirme par la pastille dans ce délai "
             "après son début perd sa salle.")
    require_checkin = fields.Boolean(
        string="Libérer les réservations non confirmées", default=True,
        help="Décoché : une réservation garde sa salle même si personne ne vient.")
    durations = fields.Char(
        string="Durées proposées (min)", default="30,60",
        help="Les boutons « Prendre » de la porte, en minutes, séparés par des virgules.")
    event_ids = fields.One2many("calendar.event", "bf_nfc_room_id", string="Réservations")
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
                        "Salle « %(salle)s » libérée : personne n'a confirmé sa présence par la "
                        "pastille dans les %(delai)s minutes.", salle=salle.name,
                        delai=salle.checkin_delay or 10)),
                    message_type="comment", subtype_xmlid="mail.mt_note")


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    bf_nfc_room_id = fields.Many2one("bf.nfc.room", string="Salle", index=True, tracking=True)
    bf_nfc_checkin = fields.Datetime(string="Confirmée à la porte", copy=False)

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
                    "La salle « %(salle)s » est déjà réservée de %(debut)s à %(fin)s.",
                    salle=evenement.bf_nfc_room_id.name,
                    debut=fields.Datetime.context_timestamp(self, conflit.start).strftime("%H:%M"),
                    fin=fields.Datetime.context_timestamp(self, conflit.stop).strftime("%H:%M")))
