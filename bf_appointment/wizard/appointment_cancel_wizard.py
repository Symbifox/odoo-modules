"""The dialog behind "Cancel" on a booking, from the backend.

A booking cancelled by the CLIENT already sends a branded notice: the public
`/cancel` route does it, and has since the first version of this module. A
booking cancelled from the backend sent nothing at all — the person who did not
show up for it was the one who never heard.

This closes that half, and asks first rather than assuming: a booking is
cancelled from the backend both when a client calls to move it (tell them) and
when it was a test, a duplicate, or a slot the client already abandoned (do
not). Unticked by default, because the message leaves the building and cannot
be recalled.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfAppointmentCancel(models.TransientModel):
    _name = "bf.appointment.cancel"
    _description = "Annuler un rendez-vous"

    booking_ids = fields.Many2many(
        "resource.booking",
        string="Rendez-vous",
        required=True,
    )
    notify = fields.Boolean(
        string="Envoyer un avis d'annulation",
        default=False,
        help="Écrit à la personne qui a réservé, avec le message d'annulation "
             "brandé que ce module envoie déjà quand un client annule depuis la "
             "page de confirmation.",
    )
    notify_organizer = fields.Boolean(
        string="Prévenir aussi l'organisateur",
        default=False,
        help="Envoie sa copie à l'organisateur, comme le fait la route "
             "publique d'annulation. Décoché ici par défaut : depuis le back-office, "
             "c'est en général l'organisateur qui annule.",
    )
    reason = fields.Text(
        string="Raison",
        help="Facultative. Conservée sur le rendez-vous, et reprise dans "
             "l'avis quand il en part un.",
    )
    recipient_summary = fields.Char(compute="_compute_recipient_summary")

    @api.depends("booking_ids")
    def _compute_recipient_summary(self):
        for wizard in self:
            addresses = [
                partner.email
                for booking in wizard.booking_ids
                for partner in booking.partner_ids
                if partner.email
            ]
            # Said out loud rather than left to an empty widget: a booking made
            # from the public page without an address is exactly the case where
            # someone would tick the box and believe the client was told.
            wizard.recipient_summary = ", ".join(dict.fromkeys(addresses)) or _(
                "Personne ne sera prévenu : aucun demandeur de ces rendez-vous "
                "n'a d'adresse courriel."
            )

    def action_apply(self):
        """Record the reason, cancel, then send whatever was asked for."""
        self.ensure_one()
        if not self.booking_ids:
            raise UserError(_("Il n'y a aucun rendez-vous à annuler."))
        reason = (self.reason or "").strip()
        # ⚠️ Written BEFORE the cancellation and read back after. `action_cancel`
        # archives the booking, and the notice templates render the reason off
        # the record; writing it afterwards would put it in the database too
        # late for the message that quotes it.
        live = self.booking_ids.filtered(lambda b: b.state != "canceled")
        if reason:
            live.sudo().write({"cancellation_reason": reason[:2000]})
        live.with_context(
            no_mail_to_attendees=True,
            tracking_disable=True,
            mail_notrack=True,
        ).action_cancel()
        if self.notify or self.notify_organizer:
            live._bf_send_cancellation_notices(
                to_booker=self.notify, to_organizer=self.notify_organizer,
            )
        return {"type": "ir.actions.act_window_close"}
