"""Le fuseau que voit le client : son navigateur d'abord, sinon Montréal.

Ce que ces tests protègent est né d'un défaut en production (
2026-09-10) : la fiche de contact venait EN PREMIER dans la chaîne, et
`res_partner.tz` n'est pas rempli par la personne qu'il décrit — un lot
d'import avait posé `Europe/Paris` sur des centaines de fiches québécoises. Une
page de créneaux à Montréal a donc proposé des heures de Paris.
"""

from datetime import timedelta

import pathlib

import pytz

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("bf_appointment", "bf_appointment_fuseau")
class TestFuseauVisiteur(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="UTC"))
        attendances = [
            Command.create({
                "name": f"All day {d}", "dayofweek": str(d),
                "hour_from": 0.0, "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        # La fenêtre d'AFFICHAGE du type, c'est-à-dire le repli attendu.
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "Fenêtre Montréal",
            "attendance_ids": attendances,
            "tz": "America/Toronto",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Ressource fuseau", "calendar_id": cls.calendar.id,
            "resource_type": "material", "tz": "America/Toronto",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        # La fiche porte le fuseau d'import fautif, comme celles d'un lot
        # d'import réel.
        cls.partner = cls.env["res.partner"].create({
            "name": "Organisation d'essai",
            "email": "fuseau@test.invalid",
            "tz": "Europe/Paris",
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Type fuseau", "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id, "video_provider": "none",
            "requires_recording_consent": False,
            "combination_rel_ids": [
                Command.create({
                    "sequence": 0, "combination_id": cls.combination.id}),
            ],
        })

    def _booking(self):
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now())
        creneaux = self.booking_type._bf_candidate_slots(
            now + timedelta(hours=1), now + timedelta(days=7), limit=1)
        instant = creneaux[0].astimezone(pytz.utc).replace(tzinfo=None)
        return self.booking_type._bf_create_booking(
            instant, partners=self.partner, confirm=False)

    def test_la_fiche_du_contact_ne_pilote_plus_l_affichage(self):
        """Le défaut d'import de fuseau, en une ligne.

        Sans fuseau détecté, on retombe sur la fenêtre d'affichage du type —
        jamais sur le `Europe/Paris` que porte la fiche.
        """
        booking = self._booking()
        self.assertFalse(booking.bf_visitor_tz)
        self.assertEqual(booking.partner_id.tz, "Europe/Paris")
        self.assertEqual(booking._get_booker_display_tz(), "America/Toronto")

    def test_le_navigateur_prime(self):
        """Une personne qui réserve depuis Vancouver voit Vancouver."""
        booking = self._booking()
        booking.bf_visitor_tz = "America/Vancouver"
        self.assertEqual(booking._get_booker_display_tz(), "America/Vancouver")

    def test_une_detection_ratee_ne_deplace_personne(self):
        """`UTC` est ce que rend la détection quand elle échoue.

        L'honorer rendrait l'instant UTC brut : un créneau de 13:00 à Montréal
        annoncé 17:00.
        """
        booking = self._booking()
        booking.bf_visitor_tz = "UTC"
        self.assertEqual(booking._get_booker_display_tz(), "America/Toronto")
        booking.bf_visitor_tz = "   "
        self.assertEqual(booking._get_booker_display_tz(), "America/Toronto")

    def test_le_ics_et_les_chaines_locales_suivent_le_meme_fuseau(self):
        """Ce que la bulle disait doit être ce que le .ics et le courriel disent.

        Les trois régies de rendu du module lisent la même chaîne ; c'est leur
        divergence qui avait fait promettre une heure et en livrer une autre.
        """
        booking = self._booking()
        booking.bf_visitor_tz = "America/Vancouver"
        self.assertEqual(booking._get_ics_tzname(), "America/Vancouver")
        # `tz=False` parce que la classe d'essai pose `tz="UTC"` dans son
        # contexte : le fuseau explicite du contexte prime sur tout le reste,
        # et c'est voulu — c'est par lui que l'organisateur reçoit ses propres
        # courriels à son heure (test suivant).
        self.assertEqual(
            booking.with_context(tz=False)._bf_reader_tzname(),
            "America/Vancouver")
        _lang, tz = booking._bf_render_locale(
            self.env.ref(
                "bf_appointment.mail_template_appointment_confirmation"
            ).sudo(),
            recipient="booker",
        )
        self.assertEqual(tz, "America/Vancouver")

    def test_le_contexte_prime_encore_pour_l_organisateur(self):
        """Le fuseau posé explicitement par l'envoi reste le plus fort.

        C'est par lui que l'organisateur (Auckland) reçoit ses propres
        courriels à son heure sans contaminer ceux du client.
        """
        booking = self._booking()
        booking.bf_visitor_tz = "America/Vancouver"
        self.assertEqual(
            booking.with_context(tz="Pacific/Auckland")._bf_reader_tzname(),
            "Pacific/Auckland")

    def test_la_page_de_creneaux_detecte_le_fuseau(self):
        """La détection est EN LIGNE dans le gabarit, pas dans le bundle.

        `web.assets_frontend` est servi en bundle paresseux, injecté à
        l'événement `load` : la redirection partirait après le rendu complet
        d'une grille déjà affichée dans le mauvais fuseau.
        """
        gabarit = (pathlib.Path(__file__).resolve().parent.parent
                   / "templates" / "appointment_public.xml"
                   ).read_text(encoding="utf-8")
        self.assertIn('t-if="detect_tz"', gabarit)
        self.assertIn("resolvedOptions().timeZone", gabarit)
        self.assertIn('id="bf-tz-applied"', gabarit)
        # La garde anti-boucle : on ne redirige jamais si l'adresse porte déjà
        # un fuseau, et la redirection l'y met.
        self.assertIn('url.searchParams.has("tz")', gabarit)
