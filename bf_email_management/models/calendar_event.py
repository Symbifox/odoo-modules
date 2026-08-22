"""Track the source iMIP UID on events auto-created from email invitations.

``bf.email._maybe_ingest_calendar_invite`` stores the incoming VEVENT ``UID``
here so a later reschedule (same UID, higher SEQUENCE, arriving as a fresh
email) updates the existing tentative event instead of duplicating it, and a
``METHOD:CANCEL`` can locate and remove it.

Kept separate from ``calendar_nextcloud_sync``'s ``x_nc_uid`` (the Odoo/NC
CalDAV identity): that one is generated for our own outbound sync, while this
one is the *external* organizer's UID.
"""

from odoo import api, fields, models


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    x_imip_uid = fields.Char(
        string="iMIP UID",
        index=True,
        copy=False,
        help="UID of the calendar invitation (text/calendar part) this event "
        "was auto-created from by the email module. Used to de-duplicate "
        "reschedules and to honour cancellations.",
    )

    x_imip_organizer = fields.Char(
        string="iMIP organizer",
        index=True,
        copy=False,
        help="Bare address of the ORGANIZER that created this event through "
        "an email invitation. A later cancellation or reschedule is only "
        "honoured when it arrives from this same address: the UID alone "
        "identifies an event but proves nothing about who may change it.",
    )

    # ------------------------------------------------------------------
    # Rappel par défaut sur les événements créés dans Odoo
    # ------------------------------------------------------------------

    @api.model
    def _bf_default_alarm_minutes(self):
        """Délai du rappel posé d'office, en minutes. 0 désactive.

        Vit ici plutôt que dans le module de synchronisation parce que c'est ce
        module-ci qui porte la chaîne de rappel (fenêtre de report, cron, ntfy).
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_email_management.default_alarm_minutes", "15",
        )
        try:
            return max(0, int(str(raw).strip() or 0))
        except ValueError:
            return 0

    @api.model
    def _bf_default_alarm_ids(self):
        """Alarme par défaut, sous forme de commande Many2many.

        ⚠️ Un défaut ne s'applique QUE si le champ est absent des valeurs de
        création. C'est exactement ce qu'il faut ici : le pull de Nextcloud
        pose toujours ``alarm_ids`` explicitement, même vide, donc un événement
        tiré du .ics garde ce que le .ics dit et n'hérite jamais de ce défaut.
        Le .ics reste la source de vérité pour tout ce qui vient de lui ; ce
        défaut ne couvre que ce qu'Odoo crée lui-même, rendez-vous clients
        compris.
        """
        minutes = self._bf_default_alarm_minutes()
        if not minutes:
            return []
        alarm = self.env["calendar.alarm"].search([
            ("alarm_type", "=", "notification"),
            ("duration_minutes", "=", minutes),
        ], limit=1)
        if not alarm:
            alarm = self.env["calendar.alarm"].create({
                "name": "%s min avant" % minutes,
                "alarm_type": "notification",
                "duration": minutes,
                "interval": "minutes",
            })
        return [(6, 0, alarm.ids)]

    alarm_ids = fields.Many2many(
        default=lambda self: self._bf_default_alarm_ids(),
    )
