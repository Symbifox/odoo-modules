# -*- coding: utf-8 -*-
"""Squelette du sondage : le cycle de vie et les deux règles qui le gouvernent."""

from datetime import timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


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

    def test_schedule_creates_partners_only_at_the_end(self):
        """Un sondage sans suite ne doit pas laisser de fiches derrière lui."""
        Partner = self.env["res.partner"]
        domain = [("email", "=ilike", "obligatoire@test.invalid")]
        self.assertFalse(Partner.search(domain), "aucun contact avant la clôture")
        slots = self._add_slots(1)
        self.poll.state = "closed"
        self.poll.action_schedule(slots[0])
        self.assertTrue(Partner.search(domain), "le contact naît à la clôture")

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
        """Un bouton de vue doit rendre une action, pas un recordset."""
        slots = self._add_slots(1)
        self.poll.state = "closed"
        result = self.poll.action_schedule_and_open()
        self.assertIsInstance(result, dict, "le client web ignore un recordset")
        self.assertEqual(result.get("res_model"), "resource.booking")
        self.assertEqual(result.get("res_id"), self.poll.booking_id.id)

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
        """🔴 En « chacun propose », la plage CHOISIE prend sa retenue tout de suite.

        C'est ce que promet le libellé du réglage. Avant ce correctif, une
        plage choisie ne tenait rien tant que l'organisateur n'avait pas
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
        from odoo.exceptions import UserError

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

    def test_the_overlap_still_forms_under_blocking_holds(self):
        """🔴 La crainte qui justifiait l'ancien comportement ne tient pas.

        On lisait : « bloquer viderait le bassin au fur et à mesure, et aucun
        recoupement ne pourrait se former ». Le recoupement ne se forme pas
        dans le bassin, il se forme dans la GRILLE — et une plage déjà
        proposée sort du bassin de toute façon (`_slot_pool` écarte les plages
        existantes), retenue ou pas.
        """
        self.poll.slot_source = "open"
        self.poll.hold_mode = "blocking"
        self.poll.state = "open"
        quand = self.poll._slot_pool(self.optional_participant)[0]
        creneau = self.poll._add_slot_from_pool(self.optional_participant, quand)
        self.assertTrue(creneau.hold_event_id)
        self.assertNotIn(quand, self.poll._slot_pool(self.required_participant),
                         "une plage déjà proposée ne repasse pas par le bassin")
        self.assertIn(creneau, self.poll.slot_ids)
        self.env["appointment.poll.vote"].create({
            "participant_id": self.required_participant.id,
            "slot_id": creneau.id, "answer": "yes"})
        creneau.invalidate_recordset(["yes_count"])
        self.assertEqual(creneau.yes_count, 2, "la grille, elle, reste votable")

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
