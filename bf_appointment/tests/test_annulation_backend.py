"""Annuler un rendez-vous : l'événement reste, le créneau revient, l'avis part.

Le lot renverse deux comportements qui étaient posés exprès, et ces tests
tiennent les deux bouts.

1. L'événement d'agenda n'est plus effacé. Il l'était parce qu'un événement
   laissé derrière bloquait le créneau ; ce qui rend l'inverse possible est
   `show_as`, et rien d'autre — d'où un test qui l'éprouve pour lui-même.
2. Le cron de ménage des orphelins doit apprendre à laisser passer ce qu'on
   garde maintenant volontairement. Deux gardes, deux tests : la clause de
   statut, et le `active_test=False` du sondage — parce qu'une réservation
   annulée est ARCHIVÉE et qu'une recherche ordinaire ne la voit pas.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_annulation")
class TestAnnulationBackend(TransactionCase):

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
            "name": "24/7 Annulation",
            "attendance_ids": attendances,
            "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Salle d'essai",
            "calendar_id": cls.calendar.id,
            "resource_type": "material",
            "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Cliente d'essai",
            "email": "cliente@test.invalid",
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type d'essai",
            "duration": 1.0,
            "slot_duration": 1.0,
            "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })

    def _reservation(self, dans_le_passe=False):
        base = fields.Datetime.now()
        start = base - timedelta(days=3) if dans_le_passe else base + timedelta(days=3)
        start = start.replace(minute=0, second=0, microsecond=0)
        booking = self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([self.partner.id])],
            "combination_id": self.combination.id,
            "combination_auto_assign": False,
            "start": start,
            "duration": 1.0,
        })
        self.assertTrue(booking.meeting_id, "la réservation doit porter un événement")
        return booking

    @property
    def _statut_dispo(self):
        return "bf_event_status" in self.env["calendar.event"]._fields

    # --- ce que l'annulation fait à l'agenda ---------------------------

    def test_l_evenement_survit_a_l_annulation(self):
        """🔴 Le renversement : jusqu'ici il était effacé, systématiquement.

        Mesuré avant d'y toucher, sur un calendrier réel : TOUTES les
        réservations annulées avaient `meeting_id` vide, sans exception.
        L'agenda ne garde aucune trace d'un créneau retenu puis perdu.
        """
        if not self._statut_dispo:
            self.skipTest("bf_calendar_invite absent : l'ancien effacement s'applique")
        booking = self._reservation()
        meeting = booking.meeting_id
        booking.action_cancel()
        self.assertTrue(meeting.exists(), "l'événement doit rester à l'agenda")
        self.assertEqual(meeting.bf_event_status, "cancelled")
        self.assertEqual(booking.state, "canceled")

    def test_le_creneau_revient(self):
        """`show_as` est ce qui rend le temps, et c'est tout ce qui le rend.

        Sans ce passage à « free », garder l'événement referme le créneau pour
        une rencontre qui n'a pas lieu — exactement le défaut que l'effacement
        évitait.
        """
        if not self._statut_dispo:
            self.skipTest("bf_calendar_invite absent")
        booking = self._reservation()
        meeting = booking.meeting_id
        self.assertEqual(meeting.show_as, "busy")
        booking.action_cancel()
        self.assertEqual(meeting.show_as, "free")

    def test_le_lien_vers_l_evenement_est_conserve(self):
        """`meeting_id` reste posé, et ce n'est pas cosmétique.

        C'est lui qui empêche le cron de ménage de prendre l'événement pour un
        orphelin, et lui qui permet de remonter de la réservation annulée à la
        trace laissée à l'agenda.
        """
        if not self._statut_dispo:
            self.skipTest("bf_calendar_invite absent")
        booking = self._reservation()
        meeting = booking.meeting_id
        booking.action_cancel()
        self.assertEqual(booking.meeting_id, meeting)

    def test_deprogrammer_efface_toujours(self):
        """⚠️ « Déprogrammer » n'est pas « Annuler », et ne doit pas le devenir.

        Déprogrammer veut dire « il faut un autre créneau » : garder l'ancien
        événement afficherait une rencontre pour une réservation retournée dans
        la file.
        """
        booking = self._reservation()
        meeting = booking.meeting_id
        booking.action_unschedule()
        self.assertFalse(meeting.exists())
        self.assertFalse(booking.meeting_id)

    def test_remettre_en_attente_rend_la_reservation_a_la_file(self):
        """⚠️ Garder l'événement changeait en silence ce que fait « Remettre en
        attente ».

        `state` se calcule sur `meeting_id` : événement effacé, une réservation
        désarchivée revenait en « en attente », ce que le libellé promet. Avec
        l'événement gardé, le même clic la rendrait « planifiée » — sur un
        créneau barré, et auprès d'une cliente à qui on a dit que ça n'avait
        pas lieu.
        """
        if not self._statut_dispo:
            self.skipTest("bf_calendar_invite absent")
        booking = self._reservation()
        meeting = booking.meeting_id
        booking.action_cancel()
        self.assertTrue(meeting.exists())
        booking.toggle_active()
        self.assertFalse(meeting.exists(), "la trace barrée doit partir avec la reprise")
        self.assertEqual(booking.state, "pending")

    def test_une_ecriture_ordinaire_ne_reouvre_pas_le_creneau(self):
        """🔴 Le défaut que `_prepare_meeting_vals` referme.

        `_sync_meeting` d'OCA tourne à chaque écriture sur la réservation et
        réécrit l'événement avec `show_as="busy"` en dur. Une seule écriture
        après l'annulation — n'importe laquelle — rendait donc le créneau
        occupé pour une rencontre annulée, sans rien dire.
        """
        if not self._statut_dispo:
            self.skipTest("bf_calendar_invite absent")
        booking = self._reservation()
        meeting = booking.meeting_id
        booking.action_cancel()
        booking.sudo().write({"cancellation_reason": "une écriture de plus"})
        self.assertEqual(meeting.show_as, "free")
        self.assertEqual(meeting.bf_event_status, "cancelled")

    def test_annuler_la_rencontre_annule_la_reservation(self):
        """L'inverse du chemin ordinaire, et il faut qu'il tienne aussi.

        Une rencontre issue d'un rendez-vous EST le rendez-vous. Laisser la
        réservation vivante sous une rencontre barrée la garderait dans les
        listes « à confirmer » et laisserait partir un rappel pour une
        rencontre qui n'a pas lieu.
        """
        if not self._statut_dispo:
            self.skipTest("bf_calendar_invite absent")
        booking = self._reservation()
        meeting = booking.meeting_id
        meeting._bf_cancel()
        self.assertEqual(booking.state, "canceled")
        self.assertTrue(meeting.exists())
        self.assertEqual(meeting.show_as, "free")

    # --- le cron de ménage ---------------------------------------------

    def _evenement_annule_dans_le_passe(self):
        booking = self._reservation(dans_le_passe=True)
        meeting = booking.meeting_id
        meeting.name = "RDV - Cliente d'essai"
        booking.action_cancel()
        return booking, meeting

    def test_le_cron_ne_reprend_pas_ce_qu_on_garde(self):
        """🔴 Le cron efface « RDV - % » passés sans réservation : notre trace
        coche toutes ses cases sans être un orphelin.

        Sans la clause de statut, il supprimerait le lendemain, en silence,
        exactement ce que ce lot existe pour laisser.
        """
        if not self._statut_dispo:
            self.skipTest("bf_calendar_invite absent")
        _booking, meeting = self._evenement_annule_dans_le_passe()
        self.env["calendar.event"]._cron_cleanup_orphan_booking_events()
        self.assertTrue(meeting.exists(), "le cron a effacé la trace gardée")

    def test_le_sondage_du_cron_voit_les_reservations_archivees(self):
        """⚠️ Une réservation annulée est ARCHIVÉE.

        Le sondage « une réservation pointe-t-elle encore dessus ? » cherchait
        en `active_test` par défaut : il répondait « personne » précisément pour
        les événements qui SONT pointés. Éprouvé ici sans la clause de statut,
        pour que ce garde-là tienne tout seul.
        """
        _booking, meeting = self._evenement_annule_dans_le_passe()
        trouve = self.env["resource.booking"].sudo().with_context(
            active_test=False,
        ).search([("meeting_id", "=", meeting.id)])
        self.assertTrue(
            trouve, "la réservation archivée doit rester trouvable par son événement")
        self.assertFalse(
            self.env["resource.booking"].sudo().search([
                ("meeting_id", "=", meeting.id),
            ]),
            "et c'est bien `active_test` qui la cachait",
        )

    # --- l'avis d'annulation -------------------------------------------

    def _envois_captes(self):
        """Interceptés au niveau de `_send_appointment_email`, et pas au niveau
        de `mail.mail`.

        ⚠️ Le gabarit est `auto_delete` : la ligne `mail.mail` est DÉTRUITE dès
        la remise, donc la chercher après coup rend toujours vide — un test
        écrit là-dessus échoue sur un envoi réussi. Ce qu'on veut savoir ici,
        c'est de toute façon si l'assistant a demandé l'envoi, et lequel.
        """
        captes = []

        def espion(self_booking, template, attach_ics=True, recipient=None):
            captes.append((self_booking.id, template.id, recipient))

        return captes, patch.object(
            type(self.env["resource.booking"]),
            "_send_appointment_email",
            espion,
        )

    def test_l_assistant_n_envoie_rien_par_defaut(self):
        """🔴 La case est décochée, et « décochée » doit vouloir dire silence.

        C'est la seule chose entre une annulation et un courriel qui sort de la
        maison.
        """
        booking = self._reservation()
        wizard = self.env["bf.appointment.cancel"].create({
            "booking_ids": [Command.set([booking.id])],
        })
        self.assertFalse(wizard.notify)
        self.assertFalse(wizard.notify_organizer)
        captes, espion = self._envois_captes()
        with espion:
            wizard.action_apply()
        self.assertEqual(booking.state, "canceled")
        self.assertFalse(captes)

    def test_l_assistant_ecrit_a_la_cliente_quand_on_le_lui_demande(self):
        booking = self._reservation()
        wizard = self.env["bf.appointment.cancel"].create({
            "booking_ids": [Command.set([booking.id])],
            "notify": True,
            "reason": "Reportée au mois prochain",
        })
        captes, espion = self._envois_captes()
        with espion:
            wizard.action_apply()
        self.assertEqual(booking.cancellation_reason, "Reportée au mois prochain")
        self.assertEqual(len(captes), 1)
        gabarit = self.env.ref(
            "bf_appointment.mail_template_appointment_cancellation")
        self.assertEqual(captes[0][1], gabarit.id)
        self.assertEqual(captes[0][2], "booker")

    def test_l_assistant_nomme_qui_serait_prevenu(self):
        """La liste est montrée avant la décision, pas après."""
        booking = self._reservation()
        wizard = self.env["bf.appointment.cancel"].create({
            "booking_ids": [Command.set([booking.id])],
        })
        self.assertIn("cliente@test.invalid", wizard.recipient_summary)

    def test_une_reservation_sans_adresse_le_dit(self):
        """⚠️ Dit à l'écran plutôt que laissé à une liste vide.

        Une liste vide se lit « pas encore chargé », et c'est le seul cas où
        quelqu'un cocherait la case en croyant que la cliente sera prévenue.
        """
        muet = self.env["res.partner"].create({"name": "Sans adresse"})
        booking = self.env["resource.booking"].create({
            "type_id": self.booking_type.id,
            "partner_ids": [Command.set([muet.id])],
            "combination_id": self.combination.id,
            "combination_auto_assign": False,
            "start": (fields.Datetime.now() + timedelta(days=4)).replace(
                minute=0, second=0, microsecond=0),
            "duration": 1.0,
        })
        wizard = self.env["bf.appointment.cancel"].create({
            "booking_ids": [Command.set([booking.id])],
        })
        self.assertIn("Personne ne sera prévenu", wizard.recipient_summary)
