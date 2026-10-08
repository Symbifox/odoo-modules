# -*- coding: utf-8 -*-
"""Un compte interne sans le groupe Réservations ouvre une rencontre enregistrée.

`resource_booking` (OCA) pose `resource_booking_ids` dans le formulaire de
rencontre sans groupe. Le client web lit tous les champs d'une vue, invisibles
compris : sans la garde de `views/calendar_event_booking_access.xml`, ce compte
recevait un refus d'accès sur `resource.booking` en ouvrant n'importe quelle
rencontre, qu'elle porte un rendez-vous ou non.
"""

from odoo import Command
from odoo.tests import Form, TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_acces_rencontre")
class TestRencontreSansReservations(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_mail_to_attendees=True))
        groupes = [cls.env.ref("base.group_user").id]
        # Les autres modules du formulaire de rencontre ont leurs propres gardes ;
        # on ne mesure ici que celle des réservations.
        rencontres = cls.env.ref("bf_meeting.group_meeting_user", raise_if_not_found=False)
        if rencontres:
            groupes.append(rencontres.id)
        cls.sans = cls.env["res.users"].create({
            "name": "Agenda sans réservations", "login": "bf_agenda_sans_reservations",
            "email": "agenda.sans.reservations@example.com",
            "groups_id": [Command.set(groupes)]})
        cls.avec = cls.env["res.users"].create({
            "name": "Agenda avec réservations", "login": "bf_agenda_avec_reservations",
            "email": "agenda.avec.reservations@example.com",
            "groups_id": [Command.set(groupes + [cls.env.ref("resource_booking.group_user").id])]})
        cls.rencontre = cls.env["calendar.event"].create({
            "name": "Rencontre ordinaire", "start": "2026-10-20 14:00:00",
            "stop": "2026-10-20 15:00:00",
            "partner_ids": [Command.set([cls.sans.partner_id.id, cls.avec.partner_id.id])]})
        cls.vue = cls.env.ref("calendar.view_calendar_event_form").id

    def test_le_champ_est_reserve_au_groupe(self):
        sans = self.env["calendar.event"].with_user(self.sans).get_view(self.vue)["arch"]
        avec = self.env["calendar.event"].with_user(self.avec).get_view(self.vue)["arch"]
        self.assertNotIn("resource_booking_ids", sans)
        self.assertIn("resource_booking_ids", avec)

    def test_la_rencontre_s_ouvre_sans_le_groupe(self):
        self.assertFalse(self.sans.has_group("resource_booking.group_user"))
        with Form(self.rencontre.with_user(self.sans)) as form:
            self.assertEqual(form.name, "Rencontre ordinaire")
