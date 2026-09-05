# -*- coding: utf-8 -*-
"""Les participants du sondage sur l'événement d'agenda.

Le reste de la suite monte une ressource MATÉRIELLE. Ici la ressource est une
PERSONNE, comme en production : son partenaire entre dans la liste des
participants de l'événement, ce qui est la forme où le défaut s'est produit.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment_poll", "post_install", "-at_install")
class TestPollMeetingAttendees(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": f"All day {d}",
                "dayofweek": str(d),
                "hour_from": 0.0,
                "hour_to": 24.0,
                "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 Participants",
            "attendance_ids": attendances,
            "tz": "UTC",
        })
        cls.organisateur = cls.env["res.users"].create({
            "name": "Organisateur",
            "login": "organisateur-agenda@test.invalid",
            "email": "organisateur-agenda@test.invalid",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Organisateur",
            "calendar_id": cls.calendar.id,
            "resource_type": "user",
            "user_id": cls.organisateur.id,
            "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Topo",
            "duration": 0.5,
            "slot_duration": 0.5,
            "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "combination_rel_ids": [
                Command.create({
                    "sequence": 0, "combination_id": cls.combination.id}),
            ],
        })
        cls.poll = cls.env["appointment.poll"].create({
            "name": "Topo sur état du mandat",
            "type_id": cls.booking_type.id,
            "user_id": cls.organisateur.id,
            "max_slots": 4,
        })
        for adresse in ("un@participant.invalid", "deux@participant.invalid",
                        "trois@participant.invalid"):
            cls.env["appointment.poll.participant"].create({
                "poll_id": cls.poll.id,
                "email": adresse,
                "required": True,
            })

    def _slots(self, count=2):
        base = fields.Datetime.now() + timedelta(days=3)
        base = base.replace(minute=0, second=0, microsecond=0)
        return self.env["appointment.poll.slot"].create([
            {
                "poll_id": self.poll.id,
                "start": base + timedelta(hours=i),
                "stop": base + timedelta(minutes=30) + timedelta(hours=i),
            }
            for i in range(count)
        ])

    def _fixer(self):
        slots = self._slots(2)
        self.poll.state = "closed"
        return self.poll.action_schedule(slots[0])

    def test_every_participant_is_on_the_calendar_event(self):
        """Le contrat : qui a répondu au sondage figure dans l'agenda.

        🔴 Mesuré en production : trois personnes avaient répondu, la
        réservation les portait toutes les trois, et l'événement d'agenda n'a
        été créé qu'avec l'organisateur — un seul `calendar.attendee`, les
        trois autres posés à la main une demi-heure plus tard. Sans
        participants sur l'événement, l'agenda
        ne dit pas qui est attendu et le bouton d'invitation d'Odoo ne
        s'adresse à personne.
        """
        booking = self._fixer()
        attendus = self.poll.participant_ids.mapped("partner_id")
        self.assertEqual(len(attendus), 3, "trois contacts attendus")
        self.assertTrue(booking.meeting_id, "la réservation porte un événement")
        self.assertFalse(
            attendus - booking.meeting_id.partner_ids,
            "manquent sur l'événement : %s"
            % (attendus - booking.meeting_id.partner_ids).mapped("email"),
        )

    def test_scheduling_repairs_an_event_born_without_them(self):
        """⚠️ Le test qui DISCRIMINE, et pourquoi il lui faut une simulation.

        Sur banc propre, le module parent pose déjà la bonne liste dès la
        création : un test qui se contente de fixer la rencontre passe avec ou
        sans la garde, donc il ne prouve rien. En production, c'est l'inverse
        qui s'est produit — l'événement est né avec le seul organisateur.

        On reproduit donc cette production-là : `_prepare_meeting_vals` du
        parent est réduit à ne rendre que le partenaire de l'organisateur,
        exactement l'état observé en production. `action_schedule`
        doit s'en remettre. Retirer l'appel dans `action_schedule` fait échouer
        ce test ; c'est ce qui le rend utile.
        """
        Booking = type(self.env["resource.booking"])
        vrai = Booking._prepare_meeting_vals
        organisateur_pid = self.organisateur.partner_id.id

        def _amputé(soi):
            vals = vrai(soi)
            vals["partner_ids"] = [(4, organisateur_pid, 0)]
            return vals

        # ⚠️ TROIS mécaniques du parent posent ces participants, pas une, et
        # il faut les couper toutes les trois pour que ce test veuille dire
        # quelque chose : `_prepare_meeting_vals` à la création,
        # `_bf_ensure_meeting_attendees` juste après, et `action_confirm`
        # d'OCA qui fait `meeting.partner_ids |= booking.partner_ids`. En
        # laisser une debout rend le test vert quoi qu'on écrive ici.
        #
        # Que les trois aient échoué ensemble en production est justement ce
        # qui reste inexpliqué. Ce test ne
        # prétend pas dire pourquoi : il fixe le contrat que le sondage doit
        # tenir même quand le parent n'y arrive pas.
        vrai_confirm = Booking.action_confirm

        def _confirm_sans_effet(soi):
            resultat = vrai_confirm(soi)
            for reservation in soi:
                if reservation.meeting_id:
                    reservation.meeting_id.with_context(
                        no_mail_to_attendees=True,
                    ).partner_ids = [(6, 0, [organisateur_pid])]
            return resultat

        with patch.object(Booking, "_prepare_meeting_vals", _amputé), \
                patch.object(Booking, "_bf_ensure_meeting_attendees",
                             lambda soi: None), \
                patch.object(Booking, "action_confirm", _confirm_sans_effet):
            booking = self._fixer()

        attendus = self.poll.participant_ids.mapped("partner_id")
        self.assertTrue(booking.meeting_id)
        self.assertFalse(
            attendus - booking.meeting_id.partner_ids,
            "la clôture doit reposer les participants que la création a perdus",
        )

    def test_the_guard_alone_repairs_a_stripped_event(self):
        """La garde prise isolément, sur un événement réduit à l'organisateur."""
        booking = self._fixer()
        meeting = booking.meeting_id
        attendus = self.poll.participant_ids.mapped("partner_id")
        meeting.with_context(no_mail_to_attendees=True).write({
            "partner_ids": [Command.set(self.organisateur.partner_id.ids)],
        })
        self.assertEqual(
            meeting.partner_ids, self.organisateur.partner_id,
            "l'événement doit bien être réduit à l'organisateur",
        )
        self.poll._ensure_meeting_attendees(booking)
        self.assertFalse(
            attendus - meeting.partner_ids,
            "la garde doit reposer chaque participant du sondage",
        )
        self.assertIn(
            self.organisateur.partner_id, meeting.partner_ids,
            "et ne retirer personne au passage",
        )

    def test_putting_them_back_is_additive(self):
        """Ce que quelqu'un a ajouté à la main sur l'événement reste."""
        booking = self._fixer()
        meeting = booking.meeting_id
        invite = self.env["res.partner"].create({
            "name": "Invité ajouté à la main",
            "email": "ajout-manuel@participant.invalid",
        })
        meeting.with_context(no_mail_to_attendees=True).write({
            "partner_ids": [Command.link(invite.id)],
        })
        self.poll._ensure_meeting_attendees(booking)
        self.assertIn(invite, meeting.partner_ids)

    def test_putting_them_back_sends_nothing_to_anyone(self):
        """🔴 L'ajout doit être SILENCIEUX, et le test doit pouvoir l'infirmer.

        Odoo 18 : `calendar.event.write` avec `partner_ids` appelle
        `_send_mail_to_attendees(..., force_send=True)` sur les attendees
        NOUVEAUX — envoi immédiat, pas de file. Le seul frein est
        `no_mail_to_attendees` dans le contexte, lu tout en haut de
        `calendar.attendee._send_mail_to_attendees`.

        ⚠️ Trois instruments faux, essayés dans cet ordre, et ce qu'ils
        apprennent :
        1. compter après une clôture ordinaire ne mesure rien — sur banc propre
           le parent a déjà posé les participants, `manquants` est vide et la
           garde n'écrit pas ;
        2. compter les `mail.mail` APRÈS coup ne mesure rien non plus — ils sont
           en `auto_delete`, donc un envoi réussi ne laisse aucune ligne ;
        3. espionner `_send_mail_to_attendees` ne mesure rien : il n'est jamais
           atteint sur ce banc, et `all([])` est vrai — une assertion qui ne
           peut pas échouer.

        ⚠️ Et l'honnêteté sur ce qui reste : sur ce banc, poser des
        participants sur un événement adossé à une réservation ne produit AUCUN
        courriel, drapeau ou pas — vérifié en retirant `no_mail_to_attendees`,
        le test reste vert. Il garantit donc le RÉSULTAT (rien ne sort), pas le
        mécanisme. Le drapeau reste parce que c'est ce que le cœur d'Odoo 18 lit
        en tête de `calendar.attendee._send_mail_to_attendees`, et parce que
        l'envoi qu'il coupe est en `force_send=True` : sur un locataire où ce
        chemin s'ouvre, il n'y aurait pas de file où le rattraper.
        """
        booking = self._fixer()
        meeting = booking.meeting_id
        meeting.with_context(no_mail_to_attendees=True).write({
            "partner_ids": [Command.set(self.organisateur.partner_id.ids)],
        })
        attendus = self.poll.participant_ids.mapped("partner_id")

        # On intercepte la CRÉATION du courriel, pas son envoi : c'est le point
        # par lequel passe toute sortie, quel que soit le chemin, et il précède
        # l'`auto_delete` qui efface la preuve.
        Mail = type(self.env["mail.mail"])
        vrai_create = Mail.create
        sortants = []

        def _espion(soi, vals_list):
            cree = vrai_create(soi, vals_list)
            sortants.extend(cree.mapped("subject"))
            return cree

        with patch.object(Mail, "create", _espion):
            self.poll._ensure_meeting_attendees(booking)

        self.assertFalse(
            attendus - meeting.partner_ids,
            "la garde doit avoir réellement écrit, sinon ce test ne mesure rien",
        )
        self.assertEqual(
            sortants, [],
            "poser les participants sur l'événement a produit des courriels : %s"
            % sortants,
        )

    def test_a_whole_closing_sends_one_email_per_participant(self):
        """De bout en bout : un courriel par participant, celui du sondage."""
        self._slots(2)
        self.poll.state = "closed"
        avant = self.env["mail.mail"].search_count([])
        self.poll.action_schedule(self.poll.slot_ids[0])
        self.assertEqual(
            self.env["mail.mail"].search_count([]) - avant,
            len(self.poll.participant_ids),
            "ni plus ni moins que la confirmation « C'est fixé »",
        )
