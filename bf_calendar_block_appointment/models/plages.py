# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Les plages de rencontre ouvrent la prise de rendez-vous.

`resource_booking` offre un créneau quand il tient à la fois dans les heures du
type de rendez-vous et dans les heures libres de chaque personne requise
(heures de travail moins ses événements occupés). Une plage de rencontre
s'ajoute aux deux : aux heures de travail de la personne qui la pose
(`_attendance_intervals_batch`, seulement pendant le calcul d'une réservation),
et aux heures du type (`resource.booking._get_intervals`). Les événements
occupés qui la chevauchent la referment, puisqu'ils restent des absences.
"""

from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

from odoo.addons.resource.models.utils import Intervals


class CalendarEventType(models.Model):
    _inherit = "calendar.event.type"

    bf_opens_booking = fields.Boolean(
        string="Opens online booking",
        help="Blocks of this type add bookable time for the person who books them, "
             "on top of their working hours. They are always shown as available.")


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    def _bf_forcer_libre(self):
        """Une plage qui ouvre la prise de rendez-vous est toujours « libre ».

        Occupée, elle se refermerait elle-même (un événement occupé est une
        absence). Le cas vient surtout d'un type posé ailleurs, par exemple
        dans Nextcloud, d'où la disponibilité n'est pas relue.
        """
        a_liberer = self.filtered(
            lambda e: e.bf_block_type_id.bf_opens_booking and e.show_as != "free")
        if a_liberer:
            super(CalendarEvent, a_liberer).write({"show_as": "free"})

    @api.model_create_multi
    def create(self, vals_list):
        events = super().create(vals_list)
        events._bf_forcer_libre()
        return events

    def write(self, vals):
        res = super().write(vals)
        if {"categ_ids", "bf_block_type_id", "show_as"} & set(vals):
            self._bf_forcer_libre()
        return res

    @api.model
    def _bf_plages_ouvertes(self, utilisateurs, debut, fin):
        """``{id d'usager: Intervals}`` de ses plages de rencontre entre ``debut`` et ``fin``."""
        if not utilisateurs:
            return {}
        debut_utc = debut.astimezone(pytz.utc).replace(tzinfo=None)
        fin_utc = fin.astimezone(pytz.utc).replace(tzinfo=None)
        # Une journée entière est stockée de 8 h à 18 h UTC : la chercher par ses
        # dates, élargies d'un jour, puis la rogner.
        events = self.sudo().with_context(active_test=True).search([
            ("bf_block_type_id.bf_opens_booking", "=", True),
            ("user_id", "in", utilisateurs.ids),
            "|", "&", ("allday", "=", False), "&", ("start", "<", fin_utc), ("stop", ">", debut_utc),
            "&", ("allday", "=", True), "&",
            ("start_date", "<=", (fin_utc + timedelta(days=1)).date()),
            ("stop_date", ">=", (debut_utc - timedelta(days=1)).date()),
        ])
        if "bf_event_status" in events._fields:
            events = events.filtered(lambda e: e.bf_event_status != "cancelled")
        # La plage n'ouvre que l'agenda de qui l'a voulue : posée par la
        # personne elle-même ou acceptée par elle. Sans quoi un collègue
        # ouvrait à la réservation, par un simple événement à son nom, les
        # heures d'une autre personne.
        events = events.filtered(lambda e: e.create_uid == e.user_id or any(
            a.partner_id == e.user_id.partner_id and a.state == "accepted"
            for a in e.attendee_ids))
        vide = self.env["resource.calendar.attendance"]
        par_usager = {}
        for event in events:
            if event.allday:
                tz = pytz.timezone(event.user_id.tz or "UTC")
                d = tz.localize(datetime.combine(event.start_date, time.min))
                f = tz.localize(datetime.combine(event.stop_date + timedelta(days=1), time.min))
            else:
                d = pytz.utc.localize(event.start)
                f = pytz.utc.localize(event.stop)
            d, f = max(d, debut), min(f, fin)
            if d < f:
                par_usager.setdefault(event.user_id.id, []).append((d, f, vide))
        return {u: Intervals(iv) for u, iv in par_usager.items()}


class ResourceCalendar(models.Model):
    _inherit = "resource.calendar"

    def _attendance_intervals_batch(self, start_dt, end_dt, resources=None, domain=None,
                                    tz=None, lunch=False):
        result = super()._attendance_intervals_batch(
            start_dt, end_dt, resources=resources, domain=domain, tz=tz, lunch=lunch)
        # Seulement pendant le calcul d'une réservation : ailleurs (feuilles de
        # temps, congés, paie), une plage de rencontre n'est pas une heure de travail.
        if lunch or not self.env.context.get("analyzing_booking") or not resources:
            return result
        personnes = [r for r in resources if r.resource_type == "user" and r.user_id]
        if not personnes:
            return result
        plages = self.env["calendar.event"]._bf_plages_ouvertes(
            self.env["res.users"].browse([r.user_id.id for r in personnes]), start_dt, end_dt)
        for ressource in personnes:
            ajout = plages.get(ressource.user_id.id)
            if ajout:
                result[ressource.id] = result.get(ressource.id, Intervals([])) | ajout
        return result


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    def _get_intervals(self, start_dt, end_dt, combination=None):
        result = super()._get_intervals(start_dt, end_dt, combination=combination)
        try:
            booking_id = self.id or self._origin.id or -1
        except AttributeError:
            booking_id = -1
        combinaisons = (
            combination or self.combination_id
            or self.mapped("type_id.combination_rel_ids.combination_id")
        ).with_context(analyzing_booking=booking_id)
        personnes = combinaisons.mapped("resource_ids").filtered(
            lambda r: r.resource_type == "user" and r.user_id)
        if not personnes:
            return result
        plages = self.env["calendar.event"]._bf_plages_ouvertes(
            personnes.mapped("user_id"), start_dt, end_dt)
        if not plages:
            return result
        # Combinaison par combinaison : les plages des
        # SIENNES, moins ce qui y est occupé. L'union de toutes les plages croisée
        # avec l'union de toutes les disponibilités offrait un créneau qu'aucune
        # combinaison ne tenait.
        for combinaison in combinaisons:
            ouvertes = Intervals([])
            for ressource in combinaison.resource_ids:
                if ressource.resource_type == "user" and ressource.user_id:
                    ouvertes |= plages.get(ressource.user_id.id, Intervals([]))
            if ouvertes:
                result |= ouvertes & combinaison.with_context(
                    exclude_public_holidays=True)._get_intervals(start_dt, end_dt)
        return result


class ResourceCalendarJournees(models.Model):
    _inherit = "resource.calendar"

    @api.model
    def _calendar_event_busy_intervals(self, start_dt, end_dt, resource, analyzed_booking_id):
        """Une journée entière occupée ferme toute la journée locale de la personne.

        Stockée de 8 h à 18 h UTC, elle ne
        fermait que 4 h à 14 h à Montréal (21 h à 7 h à Auckland), pour une
        journée entière importée d'un autre agenda comme pour une journée
        entière d'Odoo. Les intervalles d'origine restent ; on y ajoute les
        journées locales complètes.
        """
        result = super()._calendar_event_busy_intervals(start_dt, end_dt, resource, analyzed_booking_id)
        utilisateur = resource.resource_type == "user" and resource.user_id.active and resource.user_id
        if not utilisateur:
            return result
        debut = start_dt.astimezone(pytz.utc).date() - timedelta(days=1)
        fin = end_dt.astimezone(pytz.utc).date() + timedelta(days=1)
        journees = self.env["calendar.event"].with_context(active_test=True).search([
            ("allday", "=", True), ("show_as", "=", "busy"),
            ("partner_ids", "=", utilisateur.partner_id.id),
            ("start_date", "<=", fin), ("stop_date", ">=", debut),
        ])
        tz = pytz.timezone(resource.tz or utilisateur.tz or "UTC")
        ajouts = []
        for event in journees:
            if event.resource_booking_ids.id == analyzed_booking_id:
                continue
            if event.user_id != utilisateur and any(
                    a.partner_id == utilisateur.partner_id and a.state == "declined"
                    for a in event.attendee_ids):
                continue
            d = tz.localize(datetime.combine(event.start_date, time.min))
            f = tz.localize(datetime.combine(event.stop_date + timedelta(days=1), time.min))
            ajouts.append((max(d, start_dt), min(f, end_dt), self.env["resource.calendar.leaves"]))
        ajouts = [a for a in ajouts if a[0] < a[1]]
        return result | Intervals(ajouts) if ajouts else result
