"""Présence en direct : qui d'autre a la fiche du billet ouverte.

Le client web signale sa présence toutes les PRESENCE_PING_SECONDS
secondes. Est « présent » quiconque a signalé depuis moins de
PRESENCE_TTL_SECONDS. Les lignes plus vieilles que PRESENCE_PURGE_MINUTES
sont effacées au passage. Les lignes ne sont lues et écrites qu'en sudo :
un agent y accède seulement par les deux méthodes du billet, qui vérifient
d'abord son droit de lecture sur le billet.
"""
from datetime import timedelta

from odoo import api, fields, models

PRESENCE_PING_SECONDS = 25
PRESENCE_TTL_SECONDS = 75
PRESENCE_PURGE_MINUTES = 10


class HelpdeskTicketPresence(models.Model):
    _name = "helpdesk.ticket.presence"
    _description = "Présence d'un agent sur un billet"
    _log_access = False

    ticket_id = fields.Many2one(
        comodel_name="helpdesk.ticket", required=True, ondelete="cascade", index=True,
    )
    user_id = fields.Many2one(
        comodel_name="res.users", required=True, ondelete="cascade",
    )
    last_seen = fields.Datetime(required=True, index=True)

    _sql_constraints = [
        ("ticket_user_uniq", "unique(ticket_id, user_id)",
         "Une seule présence par agent et par billet."),
    ]


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    def bf_presence_ping(self):
        """Signale ma présence et rend les autres agents présents.

        Réservé aux internes : un client au portail, qui lit son billet, ne
        s'affiche pas aux agents et ne voit pas leurs noms."""
        self.ensure_one()
        self.check_access("read")
        if self.env.user.share:
            return {"interval": PRESENCE_PING_SECONDS, "others": []}
        now = fields.Datetime.now()
        self.env.cr.execute(
            """
            INSERT INTO helpdesk_ticket_presence (ticket_id, user_id, last_seen)
            VALUES (%s, %s, %s)
            ON CONFLICT (ticket_id, user_id) DO UPDATE SET last_seen = EXCLUDED.last_seen
            """,
            (self.id, self.env.uid, now),
        )
        self.env.cr.execute(
            "DELETE FROM helpdesk_ticket_presence WHERE last_seen < %s",
            (now - timedelta(minutes=PRESENCE_PURGE_MINUTES),),
        )
        others = self.env["helpdesk.ticket.presence"].sudo().search([
            ("ticket_id", "=", self.id),
            ("user_id", "!=", self.env.uid),
            ("last_seen", ">=", now - timedelta(seconds=PRESENCE_TTL_SECONDS)),
        ], order="last_seen desc")
        return {
            "interval": PRESENCE_PING_SECONDS,
            "others": [
                {"id": p.user_id.id, "name": p.user_id.name} for p in others
            ],
        }

    def bf_presence_leave(self):
        """Retire ma présence quand je quitte la fiche."""
        self.env.cr.execute(
            "DELETE FROM helpdesk_ticket_presence WHERE ticket_id IN %s AND user_id = %s",
            (tuple(self.ids) or (0,), self.env.uid),
        )
        return True

    # ------------------------------------------------------------------
    # Action à raccourci clavier (Alt+W). Alt+O va sur « Assign to me »,
    # le bouton natif de helpdesk_mgmt.
    # ------------------------------------------------------------------
    def action_bf_toggle_waiting_client(self):
        for ticket in self:
            ticket.waiting_state = (
                False if ticket.waiting_state == "client" else "client"
            )
        return True
