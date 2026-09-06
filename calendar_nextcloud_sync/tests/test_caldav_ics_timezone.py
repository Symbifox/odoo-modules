# -*- coding: utf-8 -*-
"""Fuseau de l'ICS poussé vers Nextcloud (2.12.0).

Signalé en production le 2026-08-20 : Nextcloud titrait la
fiche d'un rendez-vous « 20 août 2026 21:00 UTC » et reléguait « 09:00 heure
locale » en seconde ligne. L'instant était juste; c'est le fuseau qui manquait,
et il manquait parce que nous poussions ``DTSTART:…Z``.
"""

from datetime import datetime

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("calendar_nextcloud_sync", "caldav_ics")
class TestCalDavIcsTimezone(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="UTC"))
        cls.backend = cls.env["calendar.caldav.backend"]
        cls.organisateur = cls.env["res.users"].create({
            "name": "Organisateur ICS",
            "login": "ics.organisateur@test.invalid",
            "tz": "America/Toronto",
        })

    def _event(self, **vals):
        base = {
            "name": "Rencontre ICS",
            "start": datetime(2026, 8, 20, 21, 0, 0),
            "stop": datetime(2026, 8, 20, 21, 30, 0),
            "user_id": self.organisateur.id,
            "partner_ids": [Command.clear()],
        }
        base.update(vals)
        event = self.env["calendar.event"].create(base)
        event.x_nc_uid = "ics-%d@test.invalid" % event.id
        return event

    # -- Le fuseau accompagne l'heure ---------------------------------------

    def test_dtstart_carries_a_tzid_not_utc(self):
        """🔴 Le défaut signalé : Nextcloud affichait « UTC » en tête."""
        ics = self.backend.build_ics(self._event())
        self.assertIn("DTSTART;TZID=America/Toronto:20260820T170000", ics)
        self.assertNotIn("DTSTART:20260820T210000Z", ics)

    def test_the_instant_is_unchanged(self):
        """L'invariant : on change la façon de dire l'heure, pas l'heure.

        21:00 UTC le 20 août, c'est 17:00 à Montréal (heure avancée).
        """
        ics = self.backend.build_ics(self._event())
        self.assertIn("DTSTART;TZID=America/Toronto:20260820T170000", ics)
        self.assertIn("DTEND;TZID=America/Toronto:20260820T173000", ics)

    def test_a_vtimezone_accompanies_the_tzid(self):
        """🔴 Un TZID sans VTIMEZONE vaut une heure FLOTTANTE (RFC 5545
        §3.2.19) : le rendez-vous suivrait le lecteur au lieu de rester au
        même instant. Les deux vont ensemble."""
        ics = self.backend.build_ics(self._event())
        self.assertIn("BEGIN:VTIMEZONE", ics)
        self.assertIn("TZID:America/Toronto", ics)
        self.assertLess(
            ics.index("BEGIN:VTIMEZONE"), ics.index("BEGIN:VEVENT"),
            "le VTIMEZONE doit précéder le composant qui le cite",
        )

    def test_no_tzid_without_a_vtimezone(self):
        """Le repli : quand on ne sait pas fabriquer le VTIMEZONE, on retombe
        en UTC plutôt que d'émettre un TZID orphelin."""
        backend = self.backend
        cls = type(backend)

        def _vide(self, tzname):
            return []

        # ⚠️ Restaurer par `delattr`, pas par réaffectation : la méthode vit
        # sur une classe de BASE, pas sur la classe du registre. Réaffecter la
        # laisserait posée là, et le contrôle d'intégrité d'Odoo fait alors
        # échouer TOUS les tests suivants de la classe (« Found unexpected
        # attributes on calendar.caldav.backend »).
        cls._vtimezone_lines = _vide
        try:
            ics = backend.build_ics(self._event())
        finally:
            delattr(cls, "_vtimezone_lines")
        self.assertIn("DTSTART:20260820T210000Z", ics)
        self.assertNotIn("TZID=", ics)

    def test_allday_events_keep_their_date_form(self):
        """Un « toute la journée » n'a pas de fuseau : VALUE=DATE, sans TZID."""
        event = self._event(allday=True,
                            start_date="2026-08-20", stop_date="2026-08-20")
        ics = self.backend.build_ics(event)
        self.assertIn("DTSTART;VALUE=DATE:20260820", ics)
        self.assertNotIn("BEGIN:VTIMEZONE", ics)

    # -- D'où vient le fuseau -----------------------------------------------

    def test_organizer_tz_is_used_by_default(self):
        self.assertEqual(
            self.backend._ics_tzname(self._event()), "America/Toronto")

    def test_utc_organizer_falls_back_to_utc_form(self):
        """Un organisateur en UTC ne mérite pas un VTIMEZONE : la forme
        « …Z » dit exactement la même chose en plus court."""
        self.organisateur.tz = "UTC"
        event = self._event()
        # Le défaut d'instance peut relayer; ce qui compte est qu'un `UTC`
        # explicite ne produise jamais un TZID=UTC.
        self.assertNotEqual(self.backend._ics_tzname(event), "UTC")

    def test_vtimezone_is_valid_for_a_southern_zone(self):
        """Auckland : les règles s'inversent, et c'est le fuseau réel de
        l'organisateur de Blue Fox une partie de l'année."""
        lignes = self.backend._vtimezone_lines("Pacific/Auckland")
        self.assertTrue(lignes)
        self.assertEqual(lignes[0], "BEGIN:VTIMEZONE")
        self.assertEqual(lignes[-1], "END:VTIMEZONE")
        self.assertIn("TZID:Pacific/Auckland", lignes)
        self.assertTrue(any(l.startswith("BEGIN:DAYLIGHT") for l in lignes))
        self.assertTrue(any(l.startswith("BEGIN:STANDARD") for l in lignes))

    def test_unknown_zone_degrades_instead_of_raising(self):
        self.assertEqual(self.backend._vtimezone_lines("Mars/Olympus"), [])
