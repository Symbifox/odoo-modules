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
        """Délais des rappels posés d'office, en minutes. Liste vide = aucun.

        Vit ici plutôt que dans le module de synchronisation parce que c'est ce
        module-ci qui porte la chaîne de rappel (fenêtre de report, cron, ntfy).

        Le réglage accepte plusieurs délais séparés par des virgules
        (« 1,15 » = un rappel une minute avant et un quinze minutes avant), un
        seul délai (« 15 »), ou 0 pour n'en poser aucun. La forme liste existait
        déjà en face, dans `_bf_pull_fallback_alarm_minutes` du module de
        synchronisation ; les deux lecteurs du même réglage lisent désormais la
        même grammaire.

        ⚠️ Rend une LISTE, là où la version précédente rendait un entier. Le
        seul appelant est `_bf_default_alarm_ids` juste en dessous, mais une
        surcharge écrite ailleurs contre l'ancienne signature comparerait un
        entier à une liste sans lever d'erreur — d'où le changement de nom du
        contrat dans la docstring plutôt qu'un ajustement silencieux.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_email_management.default_alarm_minutes", "15",
        )
        minutes = []
        for chunk in str(raw or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                value = int(chunk)
            except ValueError:
                # Un réglage illisible ne doit pas emporter les délais valides
                # qui l'accompagnent : on saute le morceau fautif.
                continue
            if value > 0:
                minutes.append(value)
        # Dédoublonné et trié : « 15,1,15 » ne doit pas poser deux fois la même
        # alarme, et l'ordre rend la liste lisible dans l'interface.
        return sorted(set(minutes))

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
        Alarm = self.env["calendar.alarm"]
        alarm_ids = []
        for value in minutes:
            # Rapprochement sur `duration_minutes` plutôt que création
            # systématique : sans ça, chaque délai inédit fabriquerait un
            # doublon de l'alarme d'usine (« Notification - 15 minutes ») et la
            # liste déroulante des rappels deviendrait illisible.
            alarm = Alarm.search([
                ("alarm_type", "=", "notification"),
                ("duration_minutes", "=", value),
            ], limit=1)
            if not alarm:
                alarm = Alarm.create({
                    "name": "%s min avant" % value,
                    "alarm_type": "notification",
                    "duration": value,
                    "interval": "minutes",
                })
            alarm_ids.append(alarm.id)
        return [(6, 0, alarm_ids)]

    alarm_ids = fields.Many2many(
        default=lambda self: self._bf_default_alarm_ids(),
    )
