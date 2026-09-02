"""Accusé de rappel ancré sur une identité qui survit à la fiche participant.

L'état « vu » et « reporté » ne vivait que sur ``calendar.attendee``. Or
``calendar_nextcloud_sync`` traite une série récurrente en la RASANT : dès que
le ``.ics`` réimporté porte un ``RRULE``, la récurrence et ses occurrences sont
supprimées puis recréées avec des ``id`` neufs
(``calendar_nextcloud_sync/models/calendar_event.py:747-761``). La fiche
participant part avec, et son ``bf_dismissed_at`` aussi. Le rappel déjà écarté
repart alors sur tous les postes, et comme sa charge utile ``bus.bus`` porte
un ``timer`` négatif, il repart immédiatement.

Mesuré le 2026-08-31 sur une base de production : deux séries récurrentes ont
vu chacune leurs 719 occurrences détruites puis recréées, à une heure
d'intervalle, chaque fois suivies d'une poussée portant un ``timer`` négatif
(``-212``, puis ``-215``).

La clé retenue ne tient donc à aucun ``id`` : c'est l'UID CalDAV de la série,
que le ``.ics`` conserve d'un import à l'autre, plus l'heure de début de
l'occurrence. ⚠️ Elle est délibérément une chaîne et non un lien typé : ce
module ne dépend pas de ``calendar_nextcloud_sync``, et un Many2one le rendrait
obligatoire partout où il est installé.
"""

from datetime import timedelta

from odoo import api, fields, models


class BfCalendarReminderAck(models.Model):
    _name = "bf.calendar.reminder.ack"
    _description = "Accusé de rappel calendrier (ancré hors de la fiche participant)"
    _rec_name = "reminder_key"

    partner_id = fields.Many2one(
        "res.partner", string="Participant", required=True,
        ondelete="cascade", index=True,
    )
    reminder_key = fields.Char(
        string="Clé de rappel", required=True, index=True,
        help="Identité stable de l'occurrence : « nc:<uid>@<début> » quand la "
             "série vient d'un .ics, « odoo:<id> » sinon.",
    )
    occurrence_start = fields.Datetime(string="Début de l'occurrence", index=True)
    event_name = fields.Char(string="Rencontre")
    dismissed_at = fields.Datetime(string="Marqué vu le")
    snoozed_until = fields.Datetime(string="Reporté jusqu'à")
    ntfy_pushed_at = fields.Datetime(string="Poussé sur ntfy le")

    _sql_constraints = [
        (
            "bf_reminder_ack_uniq",
            "unique(partner_id, reminder_key)",
            "Un seul accusé de rappel par participant et par occurrence.",
        ),
    ]

    # ------------------------------------------------------------------
    # Clé stable
    # ------------------------------------------------------------------

    @api.model
    def _bf_series_uid(self, event):
        """UID CalDAV de la série, ou False.

        Les occurrences générées n'ont PAS de ``x_nc_uid`` : la synchro le met
        explicitement à False sur elles pour que la détection d'orphelins ne
        les prenne pas pour des événements NC périmés. Seul l'événement de base
        de la récurrence le porte, et c'est lui qu'on va chercher.
        """
        if "x_nc_uid" not in event._fields:
            return False  # calendar_nextcloud_sync n'est pas installé
        uid = event.x_nc_uid
        if not uid and event.recurrence_id:
            base = event.recurrence_id.base_event_id
            uid = base.x_nc_uid if base else False
        return uid or False

    @api.model
    def _bf_reminder_key(self, event):
        """Identité d'une occurrence, insensible à sa destruction/recréation."""
        uid = self._bf_series_uid(event)
        if not uid:
            # Événement natif : rien ne le rase, son id fait une clé honnête.
            return "odoo:%s" % event.id
        return "nc:%s@%s" % (uid, fields.Datetime.to_string(event.start) or "")

    # ------------------------------------------------------------------
    # Lecture / écriture
    # ------------------------------------------------------------------

    @api.model
    def _bf_find(self, partner, event):
        """Accusé existant pour ce participant et cette occurrence, ou vide."""
        if not partner or not event:
            return self.browse()
        return self.sudo().search([
            ("partner_id", "=", partner.id),
            ("reminder_key", "=", self._bf_reminder_key(event)),
        ], limit=1)

    @api.model
    def _bf_record(self, partner, event, **vals):
        """Poser ou mettre à jour l'accusé. Retourne la fiche."""
        existing = self._bf_find(partner, event)
        if existing:
            existing.write(vals)
            return existing
        return self.sudo().create(dict(
            vals,
            partner_id=partner.id,
            reminder_key=self._bf_reminder_key(event),
            occurrence_start=event.start,
            event_name=event.name,
        ))

    # ------------------------------------------------------------------
    # Purge
    # ------------------------------------------------------------------

    @api.autovacuum
    def _gc_bf_reminder_acks(self):
        """Jeter les accusés dont l'occurrence est passée depuis un moment.

        Une semaine de marge : assez pour couvrir un poste rouvert après des
        vacances, trop peu pour laisser la table enfler. Un accusé sans
        ``occurrence_start`` n'a plus de sens et part avec.
        """
        cutoff = fields.Datetime.now() - timedelta(days=7)
        stale = self.sudo().search([
            "|",
            ("occurrence_start", "=", False),
            ("occurrence_start", "<", cutoff),
        ])
        if stale:
            stale.unlink()
