# -*- coding: utf-8 -*-
"""Squelette du sondage : le cycle de vie et les deux règles qui le gouvernent."""

import re
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged("bf_appointment_poll")
class TestAppointmentPoll(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
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
            "name": "24/7 Poll",
            "attendance_ids": attendances,
            "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Poll material",
            "calendar_id": cls.calendar.id,
            "resource_type": "material",
            "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Poll Type",
            "duration": 1.0,
            "slot_duration": 1.0,
            "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })
        cls.poll = cls.env["appointment.poll"].create({
            "name": "Rencontre de coordination",
            "type_id": cls.booking_type.id,
            "max_slots": 4,
        })
        cls.required_participant = cls.env["appointment.poll.participant"].create({
            "poll_id": cls.poll.id,
            "email": "obligatoire@test.invalid",
            "required": True,
        })
        cls.optional_participant = cls.env["appointment.poll.participant"].create({
            "poll_id": cls.poll.id,
            "email": "facultatif@test.invalid",
            "required": False,
        })

    def _add_slots(self, count=3):
        base = fields.Datetime.now() + timedelta(days=3)
        base = base.replace(minute=0, second=0, microsecond=0)
        return self.env["appointment.poll.slot"].create([
            {
                "poll_id": self.poll.id,
                "start": base + timedelta(hours=i),
                "stop": base + timedelta(hours=i + 1),
            }
            for i in range(count)
        ])

    # -- Jetons ------------------------------------------------------------

    def test_tokens_are_generated_and_distinct(self):
        self.assertTrue(self.poll.access_token)
        self.assertTrue(self.required_participant.access_token)
        self.assertNotEqual(
            self.required_participant.access_token,
            self.optional_participant.access_token,
            "chaque participant a SON jeton : c'est lui qui identifie qui vote",
        )

    # -- Proposition de créneaux -------------------------------------------

    def test_propose_slots_uses_parent_hook(self):
        self.poll.action_propose_slots()
        self.assertTrue(self.poll.slot_ids)
        # Égalité stricte, pas « au plus ». Un `assertLessEqual` laissait
        # passer un plafond qui s'auto-décrémentait et ne produisait que la
        # moitié des créneaux demandés : le test était vert, la fonction non.
        self.assertEqual(
            len(self.poll.slot_ids),
            self.poll.max_slots,
            "un calendrier 24/7 doit remplir le quota exactement",
        )

    def test_propose_slots_is_incremental(self):
        """Un second appel complète sans dépasser le plafond."""
        self.poll.action_propose_slots()
        self.assertEqual(len(self.poll.slot_ids), self.poll.max_slots)
        self.poll.action_propose_slots()
        self.assertEqual(
            len(self.poll.slot_ids),
            self.poll.max_slots,
            "le plafond tient sur un appel répété",
        )

    def test_propose_slots_creates_no_booking(self):
        """Proposer ne réserve rien : c'est tout l'intérêt du crochet parent."""
        Booking = self.env["resource.booking"]
        before = Booking.search_count([("type_id", "=", self.booking_type.id)])
        self.poll.action_propose_slots()
        after = Booking.search_count([("type_id", "=", self.booking_type.id)])
        self.assertEqual(before, after)

    # -- Viabilité ---------------------------------------------------------

    def test_required_no_kills_slot(self):
        slots = self._add_slots(2)
        self.env["appointment.poll.vote"].create({
            "participant_id": self.required_participant.id,
            "slot_id": slots[0].id,
            "answer": "no",
        })
        self.assertFalse(slots[0].is_viable, "un Non obligatoire écarte le créneau")
        self.assertTrue(slots[1].is_viable)

    def test_optional_no_does_not_kill_slot(self):
        slots = self._add_slots(1)
        self.env["appointment.poll.vote"].create({
            "participant_id": self.optional_participant.id,
            "slot_id": slots[0].id,
            "answer": "no",
        })
        self.assertTrue(
            slots[0].is_viable,
            "un Non facultatif se compte, mais n'écarte pas le créneau",
        )

    def test_ifneedbe_keeps_slot_viable(self):
        slots = self._add_slots(1)
        self.env["appointment.poll.vote"].create({
            "participant_id": self.required_participant.id,
            "slot_id": slots[0].id,
            "answer": "ifneedbe",
        })
        self.assertTrue(slots[0].is_viable)
        self.assertEqual(slots[0].ifneedbe_count, 1)

    def test_one_vote_per_participant_and_slot(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger

        slots = self._add_slots(1)
        self.env["appointment.poll.vote"].create({
            "participant_id": self.required_participant.id,
            "slot_id": slots[0].id,
            "answer": "yes",
        })
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.env["appointment.poll.vote"].create({
                    "participant_id": self.required_participant.id,
                    "slot_id": slots[0].id,
                    "answer": "no",
                })

    # -- Retenues dans l'agenda -------------------------------------------

    def test_holds_are_free_not_busy(self):
        """🔴 La règle qui rend l'option acceptable : une retenue ne bloque pas.

        Un événement 'busy' gèlerait les plages du sondage pour les clients qui
        réservent en direct sur la page publique. Marqué 'free', il reste
        visible dans l'agenda sans rien fermer.
        """
        slots = self._add_slots(2)
        self.poll.hold_mode = "visible"
        slots._create_hold()
        self.assertTrue(all(s.hold_event_id for s in slots))
        for slot in slots:
            self.assertEqual(slot.hold_event_id.show_as, "free")

    def test_required_no_releases_hold_immediately(self):
        self.poll.hold_mode = "visible"
        slots = self._add_slots(1)
        slots._create_hold()
        event = slots[0].hold_event_id
        self.assertTrue(event.exists())
        self.env["appointment.poll.vote"].create({
            "participant_id": self.required_participant.id,
            "slot_id": slots[0].id,
            "answer": "no",
        })
        self.assertFalse(event.exists(), "la plage se libère dès le refus")

    # -- Clôture -----------------------------------------------------------

    def test_schedule_creates_booking_through_parent(self):
        slots = self._add_slots(2)
        self.poll.state = "closed"
        booking = self.poll.action_schedule(slots[0])
        self.assertTrue(booking.exists())
        self.assertEqual(self.poll.booking_id, booking)
        self.assertEqual(self.poll.state, "scheduled")
        self.assertEqual(booking.type_id, self.booking_type)
        self.assertEqual(
            booking.bf_source, "poll",
            "la réservation doit porter sa provenance",
        )
        self.assertEqual(
            booking.bf_source_ref, "appointment.poll,%d" % self.poll.id
        )
        self.assertEqual(
            booking._bf_source_record(), self.poll,
            "la provenance doit se résoudre en retour vers le sondage",
        )

    def test_scheduling_without_a_slot_takes_the_best_ranked(self):
        """🔴 La rencontre tombait sur le premier créneau NON REJETÉ, par heure.

        « Viable » veut seulement dire qu'aucun obligatoire n'a dit Non : un
        créneau que personne n'a regardé l'est. Mesuré en prod le 2026-08-26 :
        deux « oui » sur le dernier créneau, aucune réponse sur le premier, et
        c'est le premier qui était réservé. Le classement écrit pour cette
        décision n'était appelé nulle part.
        """
        slots = self._add_slots(3)
        for participant in (self.required_participant, self.optional_participant):
            self.env["appointment.poll.vote"].create({
                "participant_id": participant.id,
                "slot_id": slots[2].id, "answer": "yes"})
        self.poll.state = "closed"
        booking = self.poll.action_schedule()
        self.assertEqual(
            booking.start, slots[2].start,
            "la rencontre doit tomber sur le créneau que le groupe a choisi, "
            "pas sur le plus proche dans le temps",
        )

    def test_the_closing_button_asks_instead_of_guessing(self):
        self._add_slots(2)
        self.poll.state = "closed"
        action = self.poll.action_schedule_and_open()
        self.assertEqual(action["res_model"], "appointment.poll.schedule.wizard")
        self.assertEqual(action["target"], "new")
        self.assertFalse(self.poll.booking_id,
                         "rien ne doit être fixé avant que la question soit posée")

    def test_the_wizard_preselects_the_best_ranked(self):
        slots = self._add_slots(3)
        self.env["appointment.poll.vote"].create({
            "participant_id": self.required_participant.id,
            "slot_id": slots[1].id, "answer": "yes"})
        self.poll.state = "closed"
        assistant = self.env["appointment.poll.schedule.wizard"].with_context(
            default_poll_id=self.poll.id).create({"poll_id": self.poll.id})
        self.assertEqual(assistant.slot_id, slots[1])
        # ⚠️ `create` remplit le nom depuis la partie locale de l'adresse quand
        # il manque : c'est « obligatoire » qui s'affiche, pas l'adresse.
        self.assertIn("Oui : obligatoire*", assistant.apercu)
        self.assertTrue(assistant.apercu.startswith("1. "),
                        "l'aperçu doit être classé, du meilleur au moins bon")

    def test_the_wizard_refuses_a_slot_from_another_poll(self):
        """⚠️ Le domaine de la vue n'autorise rien : un client bricolé poste
        l'identifiant qu'il veut."""
        self._add_slots(1)
        autre = self.env["appointment.poll"].create({
            "name": "Ailleurs", "type_id": self.booking_type.id})
        etranger = self.env["appointment.poll.slot"].create({
            "poll_id": autre.id, "start": self.poll.slot_ids[0].start,
            "stop": self.poll.slot_ids[0].stop})
        self.poll.state = "closed"
        assistant = self.env["appointment.poll.schedule.wizard"].create({
            "poll_id": self.poll.id, "slot_id": etranger.id})
        with self.assertRaises(UserError):
            assistant.action_confirm()

    def test_scheduling_from_a_slot_row(self):
        slots = self._add_slots(2)
        self.poll.state = "closed"
        slots[1].action_schedule_here()
        self.assertTrue(self.poll.booking_id)
        self.assertEqual(self.poll.booking_id.start, slots[1].start)

    def test_a_slot_reads_as_a_date_not_a_timestamp(self):
        """Ce nom se lit dans la liste déroulante de l'assistant."""
        creneau = self._add_slots(1)
        self.assertIn("·", creneau.display_name)
        self.assertNotIn("00:00:00", creneau.display_name)

    def test_schedule_creates_partners_only_at_the_end(self):
        """Un sondage sans suite ne doit pas laisser de fiches derrière lui."""
        Partner = self.env["res.partner"]
        domain = [("email", "=ilike", "obligatoire@test.invalid")]
        self.assertFalse(Partner.search(domain), "aucun contact avant la clôture")
        slots = self._add_slots(1)
        self.poll.state = "closed"
        self.poll.action_schedule(slots[0])
        self.assertTrue(Partner.search(domain), "le contact naît à la clôture")

    def test_a_blocking_hold_does_not_block_our_own_booking(self):
        """🔴 Le sondage se bloquait lui-même.

        En « réserver réellement », la plage porte un événement `busy` posé par
        le sondage. La retenue n'était libérée qu'APRÈS `_bf_create_booking`,
        qui refusait donc l'heure qu'il venait lui-même de fermer : « Aucune
        ressource n'est disponible le … ». Le mode le plus protecteur était le
        seul à ne pas pouvoir conclure. Signalé en production le 2026-08-26.

        ⚠️ Ressource UTILISATEUR, comme pour les autres contrôles de retenue :
        avec du matériel, `show_as='busy'` n'a aucun effet et ce test passerait
        sans rien prouver.
        """
        import datetime

        import pytz

        hote = self.env["res.users"].create({
            "name": "Hôte blocage", "login": "qa_hote_blocage@test.invalid"})
        ressource = self.env["resource.resource"].create({
            "name": "Hôte blocage", "calendar_id": self.calendar.id,
            "resource_type": "user", "user_id": hote.id, "tz": "UTC"})
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])]})
        btype = self.env["resource.booking.type"].create({
            "name": "Type blocage", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": self.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combo.id})]})
        poll = self.env["appointment.poll"].create({
            "name": "Blocage", "type_id": btype.id, "user_id": hote.id,
            "hold_mode": "blocking"})
        self.env["appointment.poll.participant"].create({
            "poll_id": poll.id, "email": "blocage@test.invalid", "required": True})

        debut = fields.Datetime.context_timestamp(
            poll, fields.Datetime.now()) + datetime.timedelta(hours=1)
        cible = sorted({c.astimezone(pytz.utc).replace(tzinfo=None)
                        for c in btype._bf_candidate_slots(
                            debut, debut + datetime.timedelta(days=10), limit=200)})[0]
        creneau = self.env["appointment.poll.slot"].create({
            "poll_id": poll.id, "start": cible,
            "stop": cible + datetime.timedelta(hours=1)})
        creneau._create_hold()
        self.assertEqual(creneau.hold_event_id.show_as, "busy")

        poll.state = "closed"
        booking = poll.action_schedule(creneau)
        self.assertTrue(booking.exists(),
                        "la retenue du sondage a empêché le sondage de conclure")
        self.assertEqual(booking.start, cible)
        self.assertFalse(poll.slot_ids.mapped("hold_event_id"),
                         "les retenues doivent toutes tomber une fois fixé")

    def test_scheduling_tells_every_participant(self):
        """🔴 Le sondage ne prévenait PERSONNE en fixant la rencontre.

        La confirmation brandée du parent n'est envoyée que par la page
        publique, et ne s'adresse qu'à `object.partner_id` — un seul. Il
        fallait donc attraper le bouton « Partager » d'Odoo, qui expédie à un
        client « vous a invité à accéder au/à la resource booking ».
        """
        slots = self._add_slots(1)
        self.poll.state = "closed"
        self.poll.action_schedule(slots[0])
        envois = self.env["mail.mail"].search([
            ("model", "=", "appointment.poll.participant"),
            ("res_id", "in", self.poll.participant_ids.ids),
        ])
        self.assertEqual(
            len(envois), len(self.poll.participant_ids),
            "chaque personne doit recevoir SA confirmation",
        )
        self.assertEqual(
            set(envois.mapped("res_id")), set(self.poll.participant_ids.ids))
        for envoi in envois:
            self.assertTrue(envoi.attachment_ids,
                            "la confirmation doit porter le fichier d'agenda")

    def test_the_senders_timezone_never_reaches_the_reader(self):
        """🔴 Des gens de Montréal ont reçu des heures d'Auckland.

        Le rendu consultait `env.context['tz']` en premier, donc le fuseau de
        la SESSION qui déclenche l'envoi. L'organisateur travaillant depuis la
        Nouvelle-Zélande, ses confirmations partaient à son heure à lui.
        Signalé en production le 2026-08-26.
        """
        auckland = self.env["appointment.poll"].with_context(tz="Pacific/Auckland")
        poll = auckland.browse(self.poll.id)
        participant = poll.participant_ids.browse(self.optional_participant.id)
        self.assertNotEqual(
            participant._display_tz(), "Pacific/Auckland",
            "le fuseau de l'expéditeur a repris le dessus",
        )

    def test_a_created_contact_never_inherits_the_senders_timezone(self):
        """🔴 La seconde bouche de la même fuite.

        `res.partner.tz` prend par défaut le fuseau de la session qui crée la
        fiche. En fixant la rencontre depuis la Nouvelle-Zélande, on fabriquait
        des contacts montréalais estampillés « Pacific/Auckland » — et la fiche
        reste au carnet d'adresses pour tous les courriels qui suivront.
        """
        Participant = self.env["appointment.poll.participant"]
        sans_contact = Participant.create({
            "poll_id": self.poll.id, "email": "neuf@test.invalid",
            "tz": "America/Toronto"})
        depuis_akl = sans_contact.with_context(tz="Pacific/Auckland")
        contact = depuis_akl._ensure_partners()
        self.assertEqual(
            contact.tz, "America/Toronto",
            "le contact a hérité du fuseau de qui a cliqué",
        )

    def test_a_created_contact_falls_back_when_nothing_is_known(self):
        Participant = self.env["appointment.poll.participant"]
        muet = Participant.create({
            "poll_id": self.poll.id, "email": "muet@test.invalid"})
        contact = muet.with_context(tz="Pacific/Auckland")._ensure_partners()
        self.assertTrue(contact.tz)
        self.assertNotEqual(contact.tz, "Pacific/Auckland")

    def test_an_explicit_choice_overrides_what_the_browser_says(self):
        """Le témoin du navigateur ne peut pas défaire un choix de la personne."""
        self.optional_participant.tz = "America/Toronto"
        self.optional_participant._set_tz("Europe/Paris")
        self.assertEqual(self.optional_participant.tz, "Europe/Paris")
        self.optional_participant._remember_tz("Pacific/Auckland")
        self.assertEqual(
            self.optional_participant.tz, "Europe/Paris",
            "un témoin de navigateur a écrasé un choix explicite",
        )

    def test_an_impossible_choice_is_refused(self):
        self.optional_participant.tz = "America/Toronto"
        self.assertFalse(self.optional_participant._set_tz("Mars/Olympus_Mons"))
        self.assertEqual(self.optional_participant.tz, "America/Toronto")

    def test_the_choice_list_always_holds_the_current_zone(self):
        """⚠️ Sinon quelqu'un venu d'ailleurs ne se voit pas dans sa liste."""
        Participant = self.env["appointment.poll.participant"]
        choix = dict(Participant._tz_choices("Asia/Kathmandu"))
        self.assertIn("Asia/Kathmandu", choix)
        courants = dict(Participant._tz_choices("America/Toronto"))
        self.assertIn("America/Toronto", courants)
        self.assertIn("Europe/Paris", courants)
        self.assertLess(len(courants), 60, "une liste de six cents ne se lit pas")

    def test_the_choice_list_shows_cities_not_slashes(self):
        Participant = self.env["appointment.poll.participant"]
        libelles = dict(Participant._tz_choices())
        self.assertNotIn("/", libelles["America/Toronto"])

    def test_display_tz_asks_the_person_first(self):
        self.optional_participant.tz = "Europe/Paris"
        self.assertEqual(self.optional_participant._display_tz(), "Europe/Paris")

    def test_display_tz_falls_back_to_the_contact(self):
        contact = self.env["res.partner"].create({
            "name": "Avec fuseau", "tz": "America/Vancouver"})
        self.optional_participant.write({"tz": False, "partner_id": contact.id})
        self.assertEqual(self.optional_participant._display_tz(), "America/Vancouver")

    def test_display_tz_ends_on_something_usable(self):
        """Sans rien de connu, on tombe sur le calendrier puis sur le défaut
        réglé dans les Paramètres — jamais sur rien."""
        self.optional_participant.write({"tz": False, "partner_id": False})
        import pytz
        self.assertIn(self.optional_participant._display_tz(), pytz.all_timezones)

    def test_remembering_a_timezone_never_overwrites_a_known_one(self):
        self.optional_participant.tz = "Europe/Paris"
        self.optional_participant._remember_tz("America/Toronto")
        self.assertEqual(self.optional_participant.tz, "Europe/Paris",
                         "un fuseau connu ne se fait pas écraser par un témoin")

    def test_a_bogus_timezone_is_ignored(self):
        """⚠️ Le témoin vient du navigateur : il n'engage rien."""
        self.optional_participant.tz = False
        self.optional_participant._remember_tz("Mars/Olympus_Mons")
        self.assertFalse(self.optional_participant.tz)
        self.optional_participant._remember_tz("America/Toronto")
        self.assertEqual(self.optional_participant.tz, "America/Toronto")

    def test_the_confirmation_reads_in_the_participants_timezone(self):
        slots = self._add_slots(1)
        self.required_participant.tz = "Europe/Paris"
        self.optional_participant.tz = "America/Toronto"
        self.poll.state = "closed"
        self.poll.action_schedule(slots[0])
        paris = self.poll.scheduled_display(self.required_participant)
        toronto = self.poll.scheduled_display(self.optional_participant)
        self.assertNotEqual(
            paris, toronto,
            "deux lecteurs de fuseaux différents doivent lire deux heures",
        )
        self.assertIn("Paris", paris)

    def test_scheduled_display_reads_in_the_polls_timezone(self):
        """Le fuseau où les gens ont VOTÉ, avec son étiquette."""
        slots = self._add_slots(1)
        self.poll.state = "closed"
        self.poll.action_schedule(slots[0])
        libelle = self.poll.scheduled_display()
        self.assertIn("·", libelle)
        self.assertTrue(libelle.endswith(")"),
                        "le fuseau doit être nommé : %s" % libelle)
        self.assertNotIn("00:00:00", libelle)

    def test_a_failed_confirmation_does_not_undo_the_meeting(self):
        """⚠️ À ce stade la rencontre est acquise et l'agenda est écrit : un
        courriel manqué ne doit pas la retirer."""
        slots = self._add_slots(1)
        self.poll.state = "closed"
        self.env.ref("bf_appointment_poll.mail_template_poll_scheduled").unlink()
        booking = self.poll.action_schedule(slots[0])
        self.assertTrue(booking.exists())
        self.assertEqual(self.poll.state, "scheduled")

    def test_schedule_is_idempotent(self):
        from odoo.exceptions import UserError

        slots = self._add_slots(2)
        self.poll.state = "closed"
        self.poll.action_schedule(slots[0])
        with self.assertRaises(UserError):
            self.poll.action_schedule(slots[1])

    def test_schedule_releases_all_holds(self):
        self.poll.hold_mode = "visible"
        slots = self._add_slots(3)
        slots._create_hold()
        events = slots.mapped("hold_event_id")
        self.poll.state = "closed"
        self.poll.action_schedule(slots[0])
        self.assertFalse(
            events.exists(),
            "toutes les retenues se libèrent une fois la rencontre fixée",
        )

    # -- Correctifs du QA --------------------------------------------------

    def test_open_stamps_date_opened(self):
        """Les relances s'ancrent sur l'ouverture, pas sur `write_date`.

        `write_date` bouge au moindre changement (objet corrigé, créneau
        ajouté). Les relances s'en trouveraient repoussées indéfiniment, en
        silence.
        """
        self._add_slots(2)
        self.assertFalse(self.poll.date_opened)
        self.poll.action_open()
        opened = self.poll.date_opened
        self.assertTrue(opened)
        self.poll.description = "<p>Une précision ajoutée après coup</p>"
        self.assertEqual(
            self.poll.date_opened, opened,
            "modifier le sondage ne doit pas décaler l'ancre des relances",
        )

    def test_reminders_need_an_open_date(self):
        """Sans ancre, aucune relance : jamais de rattrapage massif."""
        self._add_slots(1)
        self.poll.state = "open"  # ouverture forcée, sans date_opened
        self.assertFalse(self.poll.date_opened)
        self.poll.participant_ids._send_reminders()
        self.assertEqual(self.required_participant.reminder_count, 0)

    def test_slot_display_name(self):
        """Odoo 18 n'a plus `name_get` : le libellé passe par display_name."""
        slots = self._add_slots(1)
        self.assertTrue(slots[0].display_name)
        self.assertNotIn(
            "appointment.poll.slot", slots[0].display_name,
            "un libellé technique signale une surcharge morte",
        )

    def test_schedule_button_returns_an_action(self):
        """Un bouton de vue doit rendre une action, pas un recordset.

        🔴 Ce contrôle verrouillait aussi le fait que le bouton FIXE tout seul.
        Il ne le fait plus : il demande sur quel créneau, et c'est l'assistant
        qui conclut. Le parcours complet est éprouvé ici, de bout en bout.
        """
        self._add_slots(2)
        self.poll.state = "closed"
        demande = self.poll.action_schedule_and_open()
        self.assertIsInstance(demande, dict, "le client web ignore un recordset")
        self.assertEqual(demande.get("res_model"),
                         "appointment.poll.schedule.wizard")
        self.assertFalse(self.poll.booking_id, "rien ne doit être fixé à ce stade")

        assistant = self.env["appointment.poll.schedule.wizard"].with_context(
            **demande["context"]).create({"poll_id": self.poll.id})
        fait = assistant.action_confirm()
        self.assertEqual(fait.get("res_model"), "resource.booking")
        self.assertEqual(fait.get("res_id"), self.poll.booking_id.id)

    # -- Partage des réponses (option par sondage) --------------------------

    def test_show_votes_default_on(self):
        """Par défaut on montre qui a répondu quoi : c'est ce qui fait
        converger un sondage."""
        self.assertTrue(self.poll.show_votes)

    def test_others_votes_excludes_self(self):
        slots = self._add_slots(1)
        for participant, answer in (
            (self.required_participant, "yes"),
            (self.optional_participant, "no"),
        ):
            self.env["appointment.poll.vote"].create({
                "participant_id": participant.id,
                "slot_id": slots[0].id,
                "answer": answer,
            })
        vus = self.poll._others_votes(self.required_participant)[slots[0].id]
        noms = [ligne[0] for ligne in vus]
        self.assertEqual(len(vus), 1, "on ne se voit pas soi-même dans « les autres »")
        self.assertIn(self.optional_participant.name, noms)
        self.assertEqual(vus[0][1], "no")
        self.assertFalse(vus[0][2], "le drapeau « obligatoire » suit le participant")

    def test_others_votes_marks_required(self):
        slots = self._add_slots(1)
        self.env["appointment.poll.vote"].create({
            "participant_id": self.required_participant.id,
            "slot_id": slots[0].id,
            "answer": "ifneedbe",
        })
        vus = self.poll._others_votes(self.optional_participant)[slots[0].id]
        self.assertTrue(vus[0][2], "un obligatoire doit être signalé comme tel")

    # -- Rendu pour les courriels et la page --------------------------------

    def test_picks_left_reads_the_personal_ceiling(self):
        self.poll.write({"max_picks_per_participant": 5, "max_slots": 0})
        self.assertEqual(
            self.poll._picks_left(self.optional_participant), (5, 5))

    def test_picks_left_counts_what_the_person_already_posed(self):
        self.poll.write({"max_picks_per_participant": 5, "max_slots": 0,
                         "slot_source": "open", "state": "open"})
        for quand in self.poll._slot_pool(self.optional_participant)[:2]:
            self.poll._add_slot_from_pool(self.optional_participant, quand)
        self.assertEqual(
            self.poll._picks_left(self.optional_participant), (3, 5))

    def test_picks_left_is_cut_by_the_poll_ceiling(self):
        """🔴 Le compteur promettait plus que ce qui serait accepté.

        Sondage plafonné à huit, cinq plages déjà posées : la personne
        suivante en obtiendrait trois, et l'écran lui annonçait « 5/5 ». La
        base descend elle aussi, sinon « 3/5 » laisserait croire qu'elle en a
        déjà utilisé deux.
        """
        self.poll.write({"max_picks_per_participant": 5, "max_slots": 8})
        self._add_slots(5)
        self.assertEqual(
            self.poll._picks_left(self.optional_participant), (3, 3))

    def test_picks_left_keeps_the_smaller_of_the_two(self):
        self.poll.write({"max_picks_per_participant": 2, "max_slots": 8})
        self._add_slots(5)
        self.assertEqual(
            self.poll._picks_left(self.optional_participant), (2, 2),
            "le plafond de la personne est le plus serré, c'est lui qui parle")

    def test_picks_left_says_nothing_when_nothing_bounds(self):
        self.poll.write({"max_picks_per_participant": 0, "max_slots": 0})
        self.assertEqual(
            self.poll._picks_left(self.optional_participant), (0, 0),
            "sans plafond, la page ne doit afficher aucun compteur")

    def test_picks_left_never_goes_negative(self):
        self.poll.write({"max_picks_per_participant": 5, "max_slots": 3})
        self._add_slots(5)
        reste, _base = self.poll._picks_left(self.optional_participant)
        self.assertEqual(reste, 0)
        self.poll.state = "open"
        self.poll.slot_source = "open"
        self.assertFalse(
            self.poll._participant_can_add_slots(self.optional_participant))

    def test_vote_summary_names_who_said_what(self):
        """🔴 Les compteurs disent COMBIEN, jamais QUI.

        L'écran affichait « 2 oui, 1 si nécessaire » et rien d'autre : sur un
        sondage à plusieurs, c'est pourtant le nom qui fait trancher.
        """
        creneau = self._add_slots(1)
        self.required_participant.name = "Isabelle"
        self.optional_participant.name = "Mathieu"
        Vote = self.env["appointment.poll.vote"]
        Vote.create({"participant_id": self.required_participant.id,
                     "slot_id": creneau.id, "answer": "yes"})
        Vote.create({"participant_id": self.optional_participant.id,
                     "slot_id": creneau.id, "answer": "ifneedbe"})
        creneau.invalidate_recordset(["vote_summary"])
        self.assertEqual(
            creneau.vote_summary,
            "Oui : Isabelle* · Si nécessaire : Mathieu",
            "l'ordre encode la lecture : les oui d'abord, les non en dernier",
        )

    def test_vote_summary_marks_the_required_one(self):
        """L'astérisque est le même code que la légende de la page publique."""
        creneau = self._add_slots(1)
        self.required_participant.name = "Isabelle"
        self.optional_participant.name = "Mathieu"
        for participant in (self.required_participant, self.optional_participant):
            self.env["appointment.poll.vote"].create({
                "participant_id": participant.id, "slot_id": creneau.id,
                "answer": "no"})
        creneau.invalidate_recordset(["vote_summary"])
        self.assertIn("Isabelle*", creneau.vote_summary)
        self.assertIn("Mathieu", creneau.vote_summary)
        self.assertNotIn("Mathieu*", creneau.vote_summary)

    def test_vote_summary_falls_back_to_the_address(self):
        """Un inscrit qui n'a pas donné de nom reste identifiable."""
        creneau = self._add_slots(1)
        self.optional_participant.name = False
        self.env["appointment.poll.vote"].create({
            "participant_id": self.optional_participant.id,
            "slot_id": creneau.id, "answer": "yes"})
        creneau.invalidate_recordset(["vote_summary"])
        self.assertIn("facultatif@test.invalid", creneau.vote_summary)

    def test_vote_summary_is_empty_before_any_answer(self):
        creneau = self._add_slots(1)
        self.assertFalse(creneau.vote_summary)

    def test_duration_display(self):
        self.assertEqual(self.poll.duration_display(), "1 h")
        self.poll.type_id.duration = 0.5
        self.assertEqual(self.poll.duration_display(), "30 min")
        self.poll.type_id.duration = 1.5
        self.assertEqual(self.poll.duration_display(), "1 h 30")

    def test_duration_display_is_the_single_source(self):
        """La page et le courriel doivent annoncer la MÊME durée.

        Le gabarit rendait « 1.5 h » là où le courriel disait « 1 h 30 » pour
        la même rencontre. Deux formulations pour un seul fait, c'est le genre
        d'écart qui fait douter du reste.
        """
        self.poll.type_id.duration = 1.5
        rendu = self.poll.duration_display()
        self.assertEqual(rendu, "1 h 30")
        self.assertNotIn(".", rendu, "jamais une durée décimale à l'écran")

    def test_slot_display_is_localised(self):
        """Un nom de jour doit passer par babel, jamais par strftime.

        `strftime('%A')` suit la locale C du serveur et rendait des jours en
        anglais à des lecteurs francophones — piège déjà corrigé dans le
        module parent.
        """
        slots = self._add_slots(1)
        jour_fr = slots[0].with_context(lang="fr_CA").display_day()
        jour_en = slots[0].with_context(lang="en_CA").display_day(en=True)
        self.assertTrue(jour_fr and jour_en)
        self.assertNotEqual(
            jour_fr, jour_en,
            "le même créneau doit se lire différemment en français et en anglais",
        )
        self.assertRegex(slots[0].display_time(), r"^\d{2}:\d{2} – \d{2}:\d{2}$")

    def test_slot_tz_label_present(self):
        """Une heure sans fuseau est le défaut qui a déjà fait annoncer deux
        heures pour une même rencontre."""
        slots = self._add_slots(1)
        self.assertTrue(slots[0].display_tz_label())

    def test_close_display_empty_without_deadline(self):
        self.assertEqual(self.poll.close_display(), "")

    # -- Les trois modes de proposition ------------------------------------

    def test_default_mode_is_organizer(self):
        """Le défaut reste celui qui ne surprend personne : c'est moi qui
        propose."""
        self.assertEqual(self.poll.slot_source, "organizer")
        self.assertFalse(
            self.poll._participant_can_add_slots(self.required_participant),
            "en mode « je propose », un invité n'ajoute rien",
        )

    def test_open_mode_lets_everyone_propose(self):
        self.poll.slot_source = "open"
        self.poll.state = "open"
        self.assertTrue(
            self.poll._participant_can_add_slots(self.optional_participant)
        )
        self.assertTrue(self.poll._slot_pool(self.optional_participant))

    def test_open_mode_respects_per_person_cap(self):
        """Sans plafond par personne, quelqu'un coche trente plages et noie le
        recoupement."""
        self.poll.slot_source = "open"
        self.poll.max_picks_per_participant = 2
        self.poll.state = "open"
        pool = self.poll._slot_pool(self.optional_participant)
        for quand in pool[:2]:
            self.assertTrue(
                self.poll._add_slot_from_pool(self.optional_participant, quand)
            )
        self.assertFalse(
            self.poll._participant_can_add_slots(self.optional_participant),
            "le plafond doit mordre au 3e",
        )
        self.assertFalse(
            self.poll._add_slot_from_pool(self.optional_participant, pool[2])
        )

    def test_open_mode_respects_poll_ceiling(self):
        self.poll.slot_source = "open"
        self.poll.max_slots = 2
        self.poll.max_picks_per_participant = 0
        self.poll.state = "open"
        pool = self.poll._slot_pool(self.optional_participant)
        for quand in pool[:2]:
            self.poll._add_slot_from_pool(self.optional_participant, quand)
        self.assertFalse(
            self.poll._participant_can_add_slots(self.optional_participant)
        )

    def test_proposed_slot_is_traced_and_counts_as_yes(self):
        self.poll.slot_source = "open"
        self.poll.state = "open"
        quand = self.poll._slot_pool(self.optional_participant)[0]
        creneau = self.poll._add_slot_from_pool(self.optional_participant, quand)
        self.assertEqual(creneau.proposed_by_id, self.optional_participant)
        self.assertEqual(self.optional_participant.proposed_count, 1)
        self.assertEqual(
            creneau.yes_count, 1,
            "une plage proposée naît avec l'appui de qui l'a proposée",
        )

    def test_arbitrary_datetime_is_refused(self):
        """🔴 La date vient d'un formulaire public : elle ne vaut rien.

        Sans ce contrôle, un formulaire trafiqué poserait une rencontre à 3 h
        du matin un dimanche dans l'agenda de l'organisateur.
        """
        self.poll.slot_source = "open"
        self.poll.state = "open"
        hors_bassin = fields.Datetime.now() + timedelta(days=400)
        self.assertFalse(
            self.poll._add_slot_from_pool(self.optional_participant, hors_bassin),
            "une date hors du bassin réel doit être refusée",
        )
        self.assertFalse(self.poll.slot_ids)

    def test_closed_poll_accepts_no_proposal(self):
        self.poll.slot_source = "open"
        self.poll.state = "closed"
        self.assertFalse(
            self.poll._participant_can_add_slots(self.optional_participant)
        )

    def test_joining_an_existing_slot_builds_the_overlap(self):
        """🔴 Le cœur du mode « chacun propose ».

        Si chaque personne repartait sur son propre enregistrement, deux
        disponibilités identiques ne se rencontreraient jamais et le
        recoupement ne se formerait pas. Rejoindre est donc distinct
        d'ajouter — et ne consomme pas le quota de propositions.
        """
        self.poll.slot_source = "open"
        self.poll.max_picks_per_participant = 1
        self.poll.state = "open"
        quand = self.poll._slot_pool(self.optional_participant)[0]
        creneau = self.poll._add_slot_from_pool(self.optional_participant, quand)
        self.assertEqual(len(self.poll.slot_ids), 1)

        # L'obligatoire choisit LA MÊME plage : même enregistrement, deux oui.
        rejoint = self.poll._add_slot_from_pool(self.required_participant, quand)
        self.assertEqual(rejoint, creneau, "on rejoint, on ne duplique pas")
        self.assertEqual(len(self.poll.slot_ids), 1)
        self.assertEqual(creneau.yes_count, 2)
        self.assertEqual(
            self.required_participant.proposed_count, 0,
            "rejoindre ne consomme pas le quota de propositions",
        )

    def test_joining_is_possible_once_the_cap_is_spent(self):
        self.poll.slot_source = "open"
        self.poll.max_picks_per_participant = 1
        self.poll.state = "open"
        pool = self.poll._slot_pool(self.optional_participant)
        self.poll._add_slot_from_pool(self.optional_participant, pool[0])
        # Ana épuise son quota, puis une plage d'autrui apparaît.
        autre = self.poll._add_slot_from_pool(self.required_participant, pool[1])
        self.assertTrue(autre)
        self.assertTrue(
            self.poll._add_slot_from_pool(self.optional_participant, pool[1]),
            "quota épuisé ou non, on peut toujours se rallier",
        )
        self.assertEqual(autre.yes_count, 2)

    def test_joining_does_not_overwrite_a_nuanced_answer(self):
        """« Si nécessaire » porte plus d'information que notre déduction."""
        self.poll.slot_source = "open"
        self.poll.state = "open"
        quand = self.poll._slot_pool(self.optional_participant)[0]
        creneau = self.poll._add_slot_from_pool(self.optional_participant, quand)
        self.env["appointment.poll.vote"].create({
            "participant_id": self.required_participant.id,
            "slot_id": creneau.id, "answer": "ifneedbe",
        })
        self.poll._add_slot_from_pool(self.required_participant, quand)
        vote = self.env["appointment.poll.vote"].search([
            ("participant_id", "=", self.required_participant.id),
            ("slot_id", "=", creneau.id)])
        self.assertEqual(vote.answer, "ifneedbe")

    # -- Amorce par un invité ----------------------------------------------

    def test_designated_seeder_is_the_only_one_who_can_seed(self):
        """Désigner quelqu'un évite que le hasard de la boîte de réception
        décide qui cadre la rencontre."""
        self.poll.slot_source = "seeder"
        self.poll.seeder_participant_id = self.required_participant
        self.poll.state = "open"
        self.assertTrue(
            self.poll._participant_can_add_slots(self.required_participant)
        )
        self.assertFalse(
            self.poll._participant_can_add_slots(self.optional_participant),
            "un invité non désigné ne pose pas la grille",
        )

    def test_undesignated_seeder_is_whoever_answers_first(self):
        self.poll.slot_source = "seeder"
        self.poll.state = "open"
        self.assertTrue(
            self.poll._participant_can_add_slots(self.optional_participant)
        )
        quand = self.poll._slot_pool(self.optional_participant)[0]
        self.poll._add_slot_from_pool(self.optional_participant, quand)
        self.assertEqual(self.poll.seeded_by_id, self.optional_participant)

    def test_grid_freezes_after_seeding(self):
        """Y compris pour celui qui l'a posée : sinon il continue de bouger le
        cadre sous les suivants."""
        self.poll.slot_source = "seeder"
        self.poll.state = "open"
        quand = self.poll._slot_pool(self.optional_participant)[0]
        self.poll._add_slot_from_pool(self.optional_participant, quand)
        self.assertFalse(
            self.poll._participant_can_add_slots(self.optional_participant)
        )
        self.assertFalse(
            self.poll._participant_can_add_slots(self.required_participant)
        )

    def test_waiting_for_seeder_message(self):
        self.poll.slot_source = "seeder"
        self.poll.seeder_participant_id = self.required_participant
        self.poll.state = "open"
        self.assertTrue(self.poll._waiting_for_seeder())
        quand = self.poll._slot_pool(self.required_participant)[0]
        self.poll._add_slot_from_pool(self.required_participant, quand)
        self.assertFalse(self.poll._waiting_for_seeder())

    def test_open_refuses_preset_grid_in_seeder_mode(self):
        from odoo.exceptions import UserError

        self.poll.slot_source = "seeder"
        self._add_slots(1)
        with self.assertRaises(UserError):
            self.poll.action_open()

    # -- Retenue d'agenda à trois niveaux ----------------------------------

    def test_hold_is_off_by_default(self):
        self.assertEqual(self.poll.hold_mode, "none")
        slots = self._add_slots(2)
        slots._create_hold()
        self.assertFalse(
            slots.mapped("hold_event_id"),
            "aucune retenue tant que le réglage est à « aucune »",
        )

    def test_visible_hold_does_not_block(self):
        """Le niveau « visible » laisse passer les réservations publiques."""
        self.poll.hold_mode = "visible"
        slots = self._add_slots(1)
        slots._create_hold()
        self.assertEqual(slots[0].hold_event_id.show_as, "free")

    def test_blocking_hold_really_reserves(self):
        """🔴 Le niveau « bloquant » ferme réellement la plage.

        C'est la différence qui compte pour une rencontre sensible, et c'est
        aussi pourquoi ce n'est jamais le défaut.
        """
        self.poll.hold_mode = "blocking"
        slots = self._add_slots(1)
        slots._create_hold()
        self.assertEqual(slots[0].hold_event_id.show_as, "busy")

    def test_open_mode_holds_at_selection(self):
        """En « chacun propose », la plage CHOISIE prend sa retenue tout de suite.

        C'est ce que promet le libellé du réglage. Avant 18.0.1.4.0, une plage
        choisie ne tenait rien tant que l'organisateur n'avait pas
        présélectionné à la main, et « réserver réellement » ne réservait rien.
        """
        self.poll.slot_source = "open"
        self.poll.hold_mode = "blocking"
        self.poll.state = "open"
        pool = self.poll._slot_pool(self.optional_participant)
        for quand in pool[:3]:
            self.poll._add_slot_from_pool(self.optional_participant, quand)
        self.assertEqual(
            len(self.poll.slot_ids.mapped("hold_event_id")), 3,
            "chaque plage choisie doit tenir sa place dans l'agenda",
        )
        self.assertEqual(
            set(self.poll.slot_ids.mapped("hold_event_id.show_as")), {"busy"})

    def test_shortlist_button_is_now_a_catch_up(self):
        """Le bouton reste utile : il repose une retenue libérée entre-temps."""
        self.poll.slot_source = "open"
        self.poll.hold_mode = "blocking"
        self.poll.state = "open"
        quand = self.poll._slot_pool(self.optional_participant)[0]
        creneau = self.poll._add_slot_from_pool(self.optional_participant, quand)
        creneau._release_hold()
        self.assertFalse(creneau.hold_event_id)
        with self.assertRaises(UserError):
            self.poll.action_hold_shortlist()  # rien de présélectionné
        creneau.is_shortlisted = True
        self.poll.action_hold_shortlist()
        self.assertTrue(creneau.hold_event_id)

    def test_blocking_hold_actually_closes_the_slot(self):
        """🔴 « Réserver réellement » doit réellement fermer la plage.

        ⚠️ Ce test monte une ressource UTILISATEUR, et pas la ressource
        matérielle du reste de la classe. C'est indispensable : un
        `calendar.event` marqué `busy` n'entre dans le calcul de disponibilité
        que lorsque la ressource est une personne (`resource_calendar.
        _calendar_event_busy_intervals`). Avec une ressource matérielle, la
        retenue n'a aucun effet et le test passerait en ne prouvant rien —
        c'est exactement ce qui est arrivé à la première sonde.
        """
        import datetime

        import pytz

        hote = self.env["res.users"].create({
            "name": "Hôte QA", "login": "qa_hote_bloc@test.invalid"})
        ressource = self.env["resource.resource"].create({
            "name": "Hôte QA", "calendar_id": self.calendar.id,
            "resource_type": "user", "user_id": hote.id, "tz": "UTC"})
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])]})
        btype = self.env["resource.booking.type"].create({
            "name": "Type bloquant", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": self.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combo.id})]})
        poll = self.env["appointment.poll"].create({
            "name": "Bloquant", "type_id": btype.id, "user_id": hote.id,
            "hold_mode": "blocking"})

        debut = fields.Datetime.context_timestamp(
            poll, fields.Datetime.now()) + datetime.timedelta(hours=1)
        fin = debut + datetime.timedelta(days=10)

        def offertes():
            return {c.astimezone(pytz.utc).replace(tzinfo=None)
                    for c in btype._bf_candidate_slots(debut, fin, limit=200)}

        cible = sorted(offertes())[0]
        creneau = self.env["appointment.poll.slot"].create({
            "poll_id": poll.id, "start": cible,
            "stop": cible + datetime.timedelta(hours=1)})
        creneau._create_hold()
        self.assertEqual(creneau.hold_event_id.show_as, "busy")
        self.assertNotIn(
            cible, offertes(),
            "une retenue bloquante doit retirer la plage des disponibilités",
        )

        # Contre-épreuve dans le MÊME montage : « visible » ne ferme rien.
        poll.hold_mode = "visible"
        libre = sorted(offertes())[0]
        autre = self.env["appointment.poll.slot"].create({
            "poll_id": poll.id, "start": libre,
            "stop": libre + datetime.timedelta(hours=1)})
        autre._create_hold()
        self.assertEqual(autre.hold_event_id.show_as, "free")
        self.assertIn(
            libre, offertes(),
            "une retenue visible ne doit fermer aucune plage",
        )

    # -- Classement par recoupement ----------------------------------------

    def test_ranked_slots_puts_the_decision_first(self):
        slots = self._add_slots(3)
        Vote = self.env["appointment.poll.vote"]
        # slot 0 : refusé par un obligatoire → non viable
        Vote.create({"participant_id": self.required_participant.id,
                     "slot_id": slots[0].id, "answer": "no"})
        # slot 1 : un oui facultatif seulement
        Vote.create({"participant_id": self.optional_participant.id,
                     "slot_id": slots[1].id, "answer": "yes"})
        # slot 2 : tout le monde, dont l'obligatoire
        for p in (self.required_participant, self.optional_participant):
            Vote.create({"participant_id": p.id, "slot_id": slots[2].id,
                         "answer": "yes"})
        classe = self.poll._ranked_slots()
        self.assertEqual(classe[0], slots[2], "le mieux couvert vient en tête")
        self.assertEqual(classe[-1], slots[0], "le non viable ferme la marche")

    # -- Sens de la dépendance --------------------------------------------

    def test_parent_does_not_reference_this_module(self):
        """🔴 L'invariant structurant : la flèche ne va que dans un sens.

        Si un champ de `resource.booking` ou `resource.booking.type` pointait
        vers un modèle d'ici, `bf_appointment` deviendrait indissociable de son
        satellite — et ça ne se verrait QUE sur une installation neuve.
        """
        for model_name in ("resource.booking", "resource.booking.type"):
            for fname, field in self.env[model_name]._fields.items():
                comodel = getattr(field, "comodel_name", None) or ""
                self.assertFalse(
                    comodel.startswith("appointment.poll"),
                    f"{model_name}.{fname} pointe vers « {comodel} »",
                )


@tagged("bf_appointment_poll")
class TestPollInvitationsAndSelfSignup(TestAppointmentPoll):
    """Ouvrir sans écrire, et le lien où les gens s'inscrivent eux-mêmes."""

    # -- A. Ouvrir sans envoyer --------------------------------------------

    def _sent_count(self):
        return self.env["mail.mail"].search_count([
            ("model", "=", "appointment.poll.participant"),
            ("res_id", "in", self.poll.participant_ids.ids),
        ])

    def test_open_sends_by_default(self):
        self.poll.action_propose_slots()
        avant = self._sent_count()
        self.poll.action_open()
        self.assertGreater(self._sent_count(), avant,
                           "le défaut historique reste l'envoi à l'ouverture")
        self.assertTrue(all(self.poll.participant_ids.mapped("invitation_sent_on")))

    def test_open_without_invitations_writes_to_nobody(self):
        self.poll.send_invitations = False
        self.poll.action_propose_slots()
        avant = self._sent_count()
        self.poll.action_open()
        self.assertEqual(self.poll.state, "open")
        self.assertEqual(self._sent_count(), avant,
                         "décoché, l'ouverture ne doit écrire à personne")
        self.assertFalse(any(self.poll.participant_ids.mapped("invitation_sent_on")))

    def test_vote_url_is_the_personal_link(self):
        p = self.required_participant
        self.assertTrue(p.vote_url.endswith("/appointment/poll/%s" % p.access_token))

    def test_send_invitations_button_skips_those_already_served(self):
        self.poll.send_invitations = False
        self.poll.action_propose_slots()
        self.poll.action_open()
        self.poll.action_send_invitations()
        premier = self._sent_count()
        self.assertTrue(all(self.poll.participant_ids.mapped("invitation_sent_on")))
        # Deuxième clic : plus personne à servir, donc un refus explicite
        # plutôt qu'un second courriel à tout le monde.
        with self.assertRaises(UserError):
            self.poll.action_send_invitations()
        self.assertEqual(self._sent_count(), premier)

    def test_send_invitations_reaches_only_the_newcomer(self):
        self.poll.send_invitations = False
        self.poll.action_propose_slots()
        self.poll.action_open()
        self.poll.action_send_invitations()
        avant = self._sent_count()
        self.env["appointment.poll.participant"].create({
            "poll_id": self.poll.id,
            "email": "retardataire@test.invalid",
        })
        self.poll.action_send_invitations()
        self.assertEqual(self._sent_count(), avant + 1,
                         "seul le nouveau venu reçoit une invitation")

    def test_open_refuses_an_empty_poll_without_signup_link(self):
        vide = self.env["appointment.poll"].create({
            "name": "Sans personne",
            "type_id": self.booking_type.id,
        })
        vide.action_propose_slots()
        with self.assertRaises(UserError):
            vide.action_open()

    def test_open_allows_an_empty_poll_when_the_link_is_on(self):
        vide = self.env["appointment.poll"].create({
            "name": "Sans personne, mais ouvert",
            "type_id": self.booking_type.id,
            "self_signup": True,
        })
        vide.action_propose_slots()
        vide.action_open()
        self.assertEqual(vide.state, "open")

    # -- B. Inscription libre ----------------------------------------------

    def _open_signup_poll(self, **extra):
        vals = {"self_signup": True}
        vals.update(extra)
        self.poll.write(vals)
        self.poll.action_propose_slots()
        self.poll.action_open()
        return self.poll

    def test_signup_url_only_exists_when_the_link_is_on(self):
        self.assertFalse(self.poll.signup_url)
        self.poll.self_signup = True
        self.assertTrue(self.poll.signup_url.endswith(
            "/appointment/poll/join/%s" % self.poll.access_token))

    def test_a_self_signed_person_is_optional(self):
        poll = self._open_signup_poll()
        p, motif = poll._self_signup_join("Inconnue", "inconnue@test.invalid")
        self.assertFalse(motif)
        self.assertTrue(p.self_signup)
        self.assertFalse(
            p.required,
            "un inconnu obligatoire rendrait toutes les plages incomplètes",
        )

    def test_a_self_signed_person_does_not_break_viability(self):
        """Le « Non » d'un inscrit libre ne doit écarter aucun créneau."""
        poll = self._open_signup_poll(slot_source="organizer")
        creneau = poll.slot_ids[0]
        p, _m = poll._self_signup_join("Inconnue", "inconnue@test.invalid")
        self.env["appointment.poll.vote"].create({
            "participant_id": p.id, "slot_id": creneau.id, "answer": "no",
        })
        creneau.invalidate_recordset(["is_viable"])
        self.assertTrue(creneau.is_viable)

    def test_rejoining_with_the_same_address_returns_the_same_seat(self):
        poll = self._open_signup_poll()
        un, _a = poll._self_signup_join("Inconnue", "inconnue@test.invalid")
        deux, _b = poll._self_signup_join("Inconnue autrement", "INCONNUE@test.invalid")
        self.assertEqual(un, deux, "l'adresse est la clé : une seule place, un seul jeton")
        self.assertEqual(len(poll.participant_ids.filtered("self_signup")), 1)

    def test_an_invited_person_who_uses_the_link_keeps_her_own_seat(self):
        poll = self._open_signup_poll()
        p, _m = poll._self_signup_join("Obligatoire", "obligatoire@test.invalid")
        self.assertEqual(p, self.required_participant)
        self.assertTrue(p.required, "passer par le lien ne dégrade pas une invitation")
        self.assertFalse(p.self_signup)

    def test_the_cap_closes_the_door(self):
        poll = self._open_signup_poll(self_signup_max=2)
        poll._self_signup_join("Une", "une@test.invalid")
        poll._self_signup_join("Deux", "deux@test.invalid")
        p, motif = poll._self_signup_join("Trois", "trois@test.invalid")
        self.assertFalse(p)
        self.assertEqual(motif, "full")

    def test_the_cap_never_locks_out_someone_already_in(self):
        poll = self._open_signup_poll(self_signup_max=1)
        une, _a = poll._self_signup_join("Une", "une@test.invalid")
        encore, motif = poll._self_signup_join("Une", "une@test.invalid")
        self.assertFalse(motif)
        self.assertEqual(une, encore)

    def test_the_domain_allowlist_is_applied(self):
        poll = self._open_signup_poll(self_signup_domains="@client.com")
        dedans, motif = poll._self_signup_join("Bonne", "jean@client.com")
        self.assertFalse(motif)
        self.assertTrue(dedans)
        dehors, motif = poll._self_signup_join("Mauvaise", "jean@ailleurs.com")
        self.assertFalse(dehors)
        self.assertEqual(motif, "domain")

    def test_an_empty_allowlist_admits_everyone(self):
        poll = self._open_signup_poll()
        p, motif = poll._self_signup_join("Quiconque", "quiconque@nimporte.ou")
        self.assertFalse(motif)
        self.assertTrue(p)

    def test_a_bad_address_is_refused(self):
        poll = self._open_signup_poll()
        p, motif = poll._self_signup_join("Bancale", "pas-une-adresse")
        self.assertFalse(p)
        self.assertEqual(motif, "invalid")

    def test_the_link_is_shut_when_the_poll_is_not_open(self):
        self.poll.self_signup = True
        self.assertEqual(self.poll.state, "draft")
        p, motif = self.poll._self_signup_join("Trop tôt", "tot@test.invalid")
        self.assertFalse(p)
        self.assertEqual(motif, "closed")

    def test_the_link_is_shut_once_the_close_date_has_passed(self):
        poll = self._open_signup_poll()
        poll.close_date = fields.Datetime.now() - timedelta(hours=1)
        p, motif = poll._self_signup_join("Trop tard", "tard@test.invalid")
        self.assertFalse(p)
        self.assertEqual(motif, "closed")

    def test_a_self_signed_person_proposes_in_open_mode(self):
        """🔴 18.0.1.4.0 — sans cela, le lien d'inscription ne servait à rien.

        La grille reste VIDE ici : en mode « chacun propose », un sondage déjà
        rempli jusqu'à `max_slots` refuserait la proposition à tout le monde,
        et le test ne prouverait plus rien sur l'inscription libre. C'est
        exactement la situation que voyait l'inscrit avant le correctif — une
        page sans bassin et sans grille, donc rien à faire.
        """
        poll = self.poll
        poll.write({"self_signup": True, "slot_source": "open"})
        poll.action_open()
        p, _m = poll._self_signup_join("Inconnue", "inconnue@test.invalid")
        self.assertTrue(poll._participant_can_add_slots(p))
        quand = poll._slot_pool(p)[0]
        creneau = poll._add_slot_from_pool(p, quand)
        self.assertTrue(creneau, "la plage choisie doit exister")
        self.assertEqual(creneau.proposed_by_id, p)
        self.assertEqual(p.proposed_count, 1)

    def test_a_self_signed_person_obeys_the_same_ceiling(self):
        """Le plafond par personne borne l'inscrit comme les autres."""
        poll = self.poll
        poll.write({"self_signup": True, "slot_source": "open",
                    "max_picks_per_participant": 2, "max_slots": 0})
        poll.action_open()
        p, _m = poll._self_signup_join("Inconnue", "inconnue@test.invalid")
        for quand in poll._slot_pool(p)[:3]:
            poll._add_slot_from_pool(p, quand)
        self.assertEqual(p.proposed_count, 2)
        self.assertFalse(poll._participant_can_add_slots(p))

    def test_a_self_signed_selection_takes_a_hold(self):
        """⚠️ Une plage choisie par un inscrit tient l'agenda de l'organisateur.

        C'est le prix assumé du mode : ce sont les domaines admis et le
        plafond d'inscriptions qui le bornent, plus un refus global.
        """
        poll = self.poll
        poll.write({"self_signup": True, "slot_source": "open",
                    "hold_mode": "blocking"})
        poll.action_open()
        p, _m = poll._self_signup_join("Inconnue", "inconnue@client.com")
        creneau = poll._add_slot_from_pool(p, poll._slot_pool(p)[0])
        self.assertEqual(creneau.hold_event_id.show_as, "busy")

    def test_the_overlap_still_forms_under_blocking_holds(self):
        """🔴 La crainte qui justifiait l'ancien comportement ne tient pas.

        On lisait : « bloquer viderait le bassin au fur et à mesure, et aucun
        recoupement ne pourrait se former ». Le recoupement ne se forme pas
        dans le bassin, il se forme dans la GRILLE — et une plage déjà
        proposée sort du bassin de toute façon (`_slot_pool` écarte les plages
        existantes), retenue ou pas. La deuxième personne la retrouve donc là
        où elle a toujours été, et vote dessus.

        ⚠️ Ressource UTILISATEUR, et non la ressource matérielle de la classe :
        un `calendar.event` marqué `busy` n'entre dans le calcul de
        disponibilité que pour une personne. Avec du matériel, la retenue
        n'aurait aucun effet et le test passerait sans rien prouver.
        """
        import datetime

        import pytz

        hote = self.env["res.users"].create({
            "name": "Hôte recoupement", "login": "qa_hote_recoup@test.invalid"})
        ressource = self.env["resource.resource"].create({
            "name": "Hôte recoupement", "calendar_id": self.calendar.id,
            "resource_type": "user", "user_id": hote.id, "tz": "UTC"})
        combo = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])]})
        btype = self.env["resource.booking.type"].create({
            "name": "Type recoupement", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": self.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combo.id})]})
        poll = self.env["appointment.poll"].create({
            "name": "Recoupement", "type_id": btype.id, "user_id": hote.id,
            "slot_source": "open", "hold_mode": "blocking", "self_signup": True})
        une = self.env["appointment.poll.participant"].create({
            "poll_id": poll.id, "email": "une@test.invalid", "required": True})
        poll.action_open()
        deux, _m = poll._self_signup_join("Deux", "deux@test.invalid")

        creneau = poll._add_slot_from_pool(une, poll._slot_pool(une)[0])
        self.assertEqual(creneau.hold_event_id.show_as, "busy")

        debut = fields.Datetime.context_timestamp(
            poll, fields.Datetime.now()) + datetime.timedelta(hours=1)
        offertes = {c.astimezone(pytz.utc).replace(tzinfo=None)
                    for c in btype._bf_candidate_slots(
                        debut, debut + datetime.timedelta(days=10), limit=200)}
        self.assertNotIn(creneau.start, offertes,
                         "la retenue bloquante ferme bien la plage")
        self.assertNotIn(creneau.start, poll._slot_pool(deux),
                         "une plage déjà proposée ne repasse pas par le bassin")

        # …et pourtant le recoupement se forme : la grille, elle, reste votable.
        self.assertIn(creneau, poll.slot_ids)
        self.env["appointment.poll.vote"].create({
            "participant_id": deux.id, "slot_id": creneau.id, "answer": "yes"})
        creneau.invalidate_recordset(["yes_count"])
        self.assertEqual(creneau.yes_count, 2)
        self.assertTrue(creneau.is_viable)

    def test_self_signup_warns_the_organizer(self):
        """🔴 Le message d'inscription ne notifiait PERSONNE.

        Il se déposait au fil du sondage, l'organisateur y étant pourtant
        abonné : `notified_partner_ids` restait vide. Il fallait ouvrir le
        sondage pour apprendre que quelqu'un était arrivé, ce qui vide de son
        intérêt un lien qu'on diffuse justement pour ne pas surveiller.
        """
        poll = self._open_signup_poll()
        poll._self_signup_join("Inconnue", "inconnue@test.invalid")
        dernier = poll.message_ids[0]
        self.assertIn("inconnue@test.invalid", dernier.body)
        self.assertIn(
            poll.user_id.partner_id, dernier.partner_ids,
            "sans destinataire, le message se dépose sans prévenir personne",
        )

    # -- C. Le code qui garde la modification -------------------------------

    def _inscrite_ayant_repondu(self):
        poll = self._open_signup_poll(slot_source="organizer")
        part, _m = poll._self_signup_join("Inconnue", "inconnue@test.invalid")
        part._record_response()
        return poll, part

    def test_an_invited_person_never_needs_a_code(self):
        """Son lien lui est parvenu par courriel : l'adresse est déjà prouvée."""
        poll = self._open_signup_poll(slot_source="organizer")
        self.required_participant._record_response()
        self.assertFalse(self.required_participant._edit_needs_otp())

    def test_a_self_signed_person_needs_a_code_once_they_answered(self):
        """🔴 Sans ce verrou, l'ADRESSE SEULE suffisait à modifier des réponses.

        `_self_signup_join` est idempotent sur l'adresse : connaître le lien
        d'inscription et l'adresse de quelqu'un donnait accès à ses réponses.
        """
        poll = self._open_signup_poll(slot_source="organizer")
        part, _m = poll._self_signup_join("Inconnue", "inconnue@test.invalid")
        self.assertFalse(part._edit_needs_otp(),
                         "rien à protéger tant qu'il n'y a pas de réponse")
        part._record_response()
        self.assertTrue(part._edit_needs_otp())

    def test_the_code_is_never_stored_in_clear(self):
        _poll, part = self._inscrite_ayant_repondu()
        code = part._otp_issue()
        self.assertRegex(code, r"^\d{6}$")
        empreinte = part.sudo().otp_hash
        self.assertTrue(empreinte)
        self.assertNotIn(code, empreinte,
                         "le code se lit dans son empreinte : elle ne protège rien")
        self.assertEqual(len(empreinte), 64, "empreinte SHA-256 attendue")

    def test_the_right_code_unlocks_and_burns_itself(self):
        _poll, part = self._inscrite_ayant_repondu()
        code = part._otp_issue()
        ok, motif = part._otp_check(code)
        self.assertTrue(ok, motif)
        self.assertFalse(part.sudo().otp_hash, "le code doit être brûlé après usage")
        encore, motif = part._otp_check(code)
        self.assertFalse(encore)
        self.assertEqual(motif, "absent")

    def test_a_wrong_code_is_refused_and_counted(self):
        _poll, part = self._inscrite_ayant_repondu()
        part._otp_issue()
        ok, motif = part._otp_check("000000" if part.sudo().otp_hash else "x")
        if ok:  # un sur un million : on retire un code sûrement différent
            part._otp_issue()
            ok, motif = part._otp_check("999999")
        self.assertFalse(ok)
        self.assertEqual(motif, "faux")
        self.assertEqual(part.sudo().otp_attempts, 1)

    def test_the_code_burns_after_five_tries(self):
        _poll, part = self._inscrite_ayant_repondu()
        code = part._otp_issue()
        faux = "%06d" % ((int(code) + 1) % 1000000)
        for _i in range(5):
            part._otp_check(faux)
        ok, motif = part._otp_check(code)
        self.assertFalse(ok, "le bon code passe encore après cinq échecs")
        self.assertEqual(motif, "brule")

    def test_an_expired_code_is_refused(self):
        _poll, part = self._inscrite_ayant_repondu()
        code = part._otp_issue()
        part.sudo().otp_expires_at = fields.Datetime.now() - timedelta(minutes=1)
        ok, motif = part._otp_check(code)
        self.assertFalse(ok)
        self.assertEqual(motif, "expire")

    def test_a_code_is_bound_to_its_own_participant(self):
        """⚠️ L'identifiant entre dans l'empreinte : sinon un code vaudrait
        pour n'importe qui à valeur égale."""
        poll, une = self._inscrite_ayant_repondu()
        deux, _m = poll._self_signup_join("Autre", "autre@test.invalid")
        deux._record_response()
        code = une._otp_issue()
        deux.sudo().write({
            "otp_hash": une.sudo().otp_hash,
            "otp_expires_at": une.sudo().otp_expires_at,
            "otp_attempts": 0,
        })
        ok, _motif = deux._otp_check(code)
        self.assertFalse(ok, "une empreinte volée a servi sur un autre participant")

    def test_resending_a_code_is_throttled(self):
        _poll, part = self._inscrite_ayant_repondu()
        part._otp_issue()
        self.assertFalse(part._otp_can_resend(),
                         "le bouton deviendrait un robinet à courriels")
        part.sudo().otp_sent_at = fields.Datetime.now() - timedelta(minutes=2)
        self.assertTrue(part._otp_can_resend())

    def test_a_self_signed_person_never_seeds_the_grid(self):
        poll = self.poll
        poll.write({"self_signup": True, "slot_source": "seeder"})
        poll.slot_ids.unlink()
        poll.action_open()
        p, _m = poll._self_signup_join("Inconnue", "inconnue@test.invalid")
        self.assertFalse(
            poll._participant_can_add_slots(p),
            "sans cela, le premier inconnu arrivé cadrerait la rencontre",
        )


@tagged("-at_install", "post_install", "bf_appointment_poll")
class TestPollSelfSignupRoutes(HttpCase, TestAppointmentPoll):
    """Les routes publiques d'inscription, vues du navigateur.

    ⚠️ Lancer avec `--db-filter` : sans lui, le `dbfilter` du conteneur
    réachemine les requêtes de test vers la base de PRODUCTION.
    """

    def _assert_sent_to_the_index(self, r):
        """Le refus conduit à l'index des rendez-vous, jamais à une page de sondage.

        ⚠️ On suit les redirections, et on juge l'URL FINALE. Le premier bond
        d'une route publique n'est pas le nôtre : `website` renvoie d'abord
        vers la même URL préfixée de la langue (`/en/...`), et notre refus ne
        s'exprime qu'au bond suivant. Un test qui lirait le seul en-tête
        Location de la première réponse mesurerait la normalisation de langue,
        pas le refus.
        """
        self.assertEqual(r.status_code, 200)
        self.assertIn("/appointment", r.url, "destination inattendue : %s" % r.url)
        self.assertNotIn("/poll", r.url,
                         "on ne doit pas rester sur le sondage : %s" % r.url)

    def _csrf_from(self, html_text):
        """Le jeton CSRF tel que le formulaire le sert.

        On le lit dans la page plutôt que de le fabriquer : `HttpCase` n'a de
        session qu'une fois authentifié, et ce parcours-ci est PUBLIC. Le lire
        vérifie du même coup que le formulaire en porte un d'utilisable.
        """
        trouve = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html_text)
        self.assertTrue(trouve, "le formulaire doit porter un jeton CSRF")
        return trouve.group(1)

    def test_the_page_is_invisible_when_the_link_is_off(self):
        """Un sondage sans inscription libre se comporte comme un jeton inconnu.

        On ne dit pas à un visiteur qu'il tient un lien valide dont la porte
        est simplement fermée.
        """
        r = self.url_open("/appointment/poll/join/%s" % self.poll.access_token)
        self._assert_sent_to_the_index(r)

    def test_an_unknown_token_goes_nowhere(self):
        r = self.url_open("/appointment/poll/join/jeton-qui-n-existe-pas")
        self._assert_sent_to_the_index(r)

    def test_the_page_shows_the_form_when_the_link_is_on(self):
        self.poll.self_signup = True
        self.poll.action_propose_slots()
        self.poll.action_open()
        r = self.url_open("/appointment/poll/join/%s" % self.poll.access_token)
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="courriel"', r.text)
        self.assertIn('name="csrf_token"', r.text)

    def test_joining_lands_on_the_persons_own_vote_page(self):
        self.poll.self_signup = True
        self.poll.action_propose_slots()
        self.poll.action_open()
        page = self.url_open("/appointment/poll/join/%s" % self.poll.access_token)
        r = self.url_open(
            "/appointment/poll/join/%s/add" % self.poll.access_token,
            data={
                "nom": "Camille Inconnue",
                "courriel": "camille@test.invalid",
                "csrf_token": self._csrf_from(page.text),
            },
            allow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)
        nouveau = self.poll.participant_ids.filtered(
            lambda p: p.email == "camille@test.invalid")
        self.assertTrue(nouveau, "la personne doit exister après le POST")
        self.assertTrue(nouveau.self_signup)
        self.assertFalse(nouveau.required)
        self.assertIn(
            "/appointment/poll/%s" % nouveau.access_token,
            r.headers["Location"],
            "on est conduit à SON lien de vote, pas à une page générique",
        )

    def test_a_refused_domain_comes_back_with_its_reason(self):
        self.poll.write({"self_signup": True, "self_signup_domains": "@client.com"})
        self.poll.action_propose_slots()
        self.poll.action_open()
        page = self.url_open("/appointment/poll/join/%s" % self.poll.access_token)
        r = self.url_open(
            "/appointment/poll/join/%s/add" % self.poll.access_token,
            data={
                "nom": "Dehors",
                "courriel": "dehors@ailleurs.com",
                "csrf_token": self._csrf_from(page.text),
            },
            allow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)
        self.assertIn("motif=domain", r.headers["Location"])
        self.assertFalse(self.poll.participant_ids.filtered(
            lambda p: p.email == "dehors@ailleurs.com"))
