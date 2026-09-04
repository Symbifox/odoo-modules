"""Le « Vu » d'un rappel doit survivre à la reconstruction de sa série.

`calendar_nextcloud_sync` traite une série récurrente en la RASANT : dès que le
`.ics` réimporté porte un `RRULE`, la récurrence et toutes ses occurrences sont
supprimées puis recréées avec des `id` neufs. La fiche participant part avec, et
avec elle le `bf_dismissed_at` qui empêchait le rappel de repartir. Tâche BF
#25127.

⚠️ Chaque cas porte son contre-épreuve : sans elle, un test qui passe ne dit pas
si la porte discrimine ou si elle laisse tout passer.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCalendarReminderAck(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env["res.users"].create({
            "name": "Rappel Testeur",
            "login": "rappel.testeur@example.org",
            "email": "rappel.testeur@example.org",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.partner = cls.user.partner_id
        cls.alarm = cls.env["calendar.alarm"].create({
            "name": "Test 15 min",
            "alarm_type": "notification",
            "duration": 15,
            "interval": "minutes",
        })
        cls.has_nc_uid = "x_nc_uid" in cls.env["calendar.event"]._fields

    def _make_event(self, start, nc_uid=None, name="Statutaire de test"):
        vals = {
            "name": name,
            "start": start,
            "stop": start + timedelta(hours=1),
            "partner_ids": [(6, 0, [self.partner.id])],
            "alarm_ids": [(6, 0, [self.alarm.id])],
        }
        if nc_uid and self.has_nc_uid:
            vals["x_nc_uid"] = nc_uid
        return self.env["calendar.event"].with_context(
            no_mail_to_attendees=True, mail_create_nolog=True,
        ).create(vals)

    def _alerts(self, event, window_seconds=3600 * 24):
        """Ce que la porte laisse passer pour cet événement, vu par l'usager.

        ⚠️ ``window_seconds`` n'est pas décoratif : la méthode de base rend []
        d'office pour tout ce qui commence au-delà de la fenêtre, et une
        occurrence de la semaine prochaine tombe hors des 24 h par défaut. Un
        test qui l'oublie croit avoir prouvé un filtrage qui n'a jamais eu lieu.
        """
        manager = self.env["calendar.alarm_manager"].with_user(self.user)
        return manager.do_check_alarm_for_one_date(
            event.start, event.with_user(self.user), 15, window_seconds,
            "notification",
        )

    # ------------------------------------------------------------------

    def test_dismiss_survit_a_la_recreation_de_la_serie(self):
        """Le rappel écarté ne revient pas quand la série est rasée et recréée."""
        if not self.has_nc_uid:
            self.skipTest("calendar_nextcloud_sync absent : pas d'UID stable à tester")
        start = fields.Datetime.now() + timedelta(minutes=10)
        uid = "test-serie-25127@example.org"
        event = self._make_event(start, nc_uid=uid)

        # Contre-épreuve : avant tout geste, la porte laisse bien passer.
        self.assertTrue(
            self._alerts(event),
            "La porte ne discrimine rien : elle refuse un rappel jamais écarté.",
        )

        self.env["calendar.attendee"].with_user(self.user).bf_dismiss(event.id)
        self.assertFalse(self._alerts(event), "Le « Vu » n'a pas fermé le rappel.")

        # La synchro rase la série et la recrée : id neufs, fiche participant
        # neuve, MÊME uid et MÊME heure de début.
        old_attendee_ids = event.attendee_ids.ids
        event.with_context(skip_nc_sync=True, dont_notify=True).unlink()
        recreated = self._make_event(start, nc_uid=uid)
        self.assertNotEqual(recreated.id, event.id)

        attendee = recreated.attendee_ids.filtered(
            lambda a: a.partner_id == self.partner
        )
        self.assertNotIn(
            attendee.id, old_attendee_ids,
            "La fiche participant n'a pas été recréée : le test ne reproduit rien.",
        )
        self.assertFalse(
            self._alerts(recreated),
            "Le rappel est revenu après la reconstruction de la série.",
        )
        # Et l'état est recopié sur la fiche neuve, pour les crons qui la lisent
        # sans rien savoir de l'accusé durable.
        self.assertTrue(
            attendee.bf_dismissed_at,
            "L'accusé durable n'a pas été recopié sur la fiche participant.",
        )

    def test_dismiss_d_une_autre_occurrence_ne_deteint_pas(self):
        """Écarter l'occurrence de cette semaine ne tait pas celle de la suivante."""
        if not self.has_nc_uid:
            self.skipTest("calendar_nextcloud_sync absent : pas d'UID stable à tester")
        uid = "test-serie-25127b@example.org"
        start = fields.Datetime.now() + timedelta(minutes=10)
        this_week = self._make_event(start, nc_uid=uid)
        next_week = self._make_event(start + timedelta(days=7), nc_uid=uid)

        self.env["calendar.attendee"].with_user(self.user).bf_dismiss(this_week.id)

        month = 3600 * 24 * 30
        self.assertFalse(self._alerts(this_week, window_seconds=month))
        self.assertTrue(
            self._alerts(next_week, window_seconds=month),
            "Le « Vu » d'une occurrence a éteint toute la série.",
        )

    def test_rappel_d_une_rencontre_terminee_est_jete(self):
        """Le rejeu de 24 h du bus ne doit pas ressortir les rappels d'hier."""
        past = fields.Datetime.now() - timedelta(hours=3)
        finished = self._make_event(past, name="Rencontre d'hier")
        self.assertFalse(
            self._alerts(finished),
            "Un rappel pour une rencontre déjà terminée est passé.",
        )

        # Contre-épreuve : la même rencontre encore en cours passe, elle.
        ongoing = self._make_event(
            fields.Datetime.now() - timedelta(minutes=10), name="Rencontre en cours",
        )
        self.assertTrue(
            self._alerts(ongoing),
            "La porte jette aussi les rencontres en cours : elle coupe trop large.",
        )

    def test_snooze_survit_a_la_recreation_de_la_serie(self):
        """Un report tient lui aussi à travers la reconstruction."""
        if not self.has_nc_uid:
            self.skipTest("calendar_nextcloud_sync absent : pas d'UID stable à tester")
        uid = "test-serie-25127c@example.org"
        start = fields.Datetime.now() + timedelta(minutes=10)
        event = self._make_event(start, nc_uid=uid)
        self.env["calendar.attendee"].with_user(self.user).bf_snooze(
            event.id, minutes=30,
        )
        self.assertFalse(self._alerts(event))

        event.with_context(skip_nc_sync=True, dont_notify=True).unlink()
        recreated = self._make_event(start, nc_uid=uid)
        self.assertFalse(
            self._alerts(recreated),
            "Le report est tombé avec la fiche participant.",
        )

    def test_payload_porte_l_horloge_du_serveur(self):
        """`sent_ms` doit accompagner chaque rappel, sinon le client ne peut pas
        reconnaître un message rejoué par le bus."""
        start = fields.Datetime.now() + timedelta(minutes=10)
        event = self._make_event(start)
        alerts = self._alerts(event)
        self.assertTrue(alerts)
        manager = self.env["calendar.alarm_manager"].with_user(self.user)
        notif = manager.do_notif_reminder(alerts[0])
        self.assertIn("sent_ms", notif)
        self.assertIsInstance(notif["sent_ms"], int)
