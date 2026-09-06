# -*- coding: utf-8 -*-
"""Fuseaux d'un .ics écrit par Outlook ou Calendly (2.14.0).

🔴 Incident du 2026-09-01. Une invitation Calendly d'une recruteuse
néo-zélandaise, tirée de Nextcloud, est arrivée dans Odoo **douze heures trop
tard** — soit exactement le décalage de la Nouvelle-Zélande.

Le .ics disait ``DTSTART;TZID=New Zealand Standard Time:20260902T090000``.
« New Zealand Standard Time » est un nom de fuseau **Windows**, pas IANA :
``pytz`` ne le connaît pas, la table Windows→IANA du moment ne le portait pas,
et le repli de l'analyseur prenait alors l'heure murale locale pour de l'UTC.

Rien n'avait l'air cassé : bon titre, bonne durée, bon numéro de téléphone.
Seule l'heure était fausse, et fausse d'une valeur parfaitement plausible.
"""

from odoo.tests import TransactionCase, tagged

# Le VTIMEZONE tel qu'Outlook l'écrit, avec ses deux sous-composants et leurs
# règles de bascule. C'est la structure exacte du .ics de l'incident.
VTIMEZONE_NZ = """BEGIN:VTIMEZONE
TZID:New Zealand Standard Time
BEGIN:STANDARD
DTSTART:16010101T030000
TZOFFSETFROM:+1300
TZOFFSETTO:+1200
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=4
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:+1200
TZOFFSETTO:+1300
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=9
END:DAYLIGHT
END:VTIMEZONE"""


def _ics(vtimezone, dtstart, dtend, tzid="New Zealand Standard Time"):
    bloc = (vtimezone + "\r\n") if vtimezone else ""
    param = (";TZID=%s" % tzid) if tzid else ""
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "METHOD:REQUEST\r\n"
        + bloc.replace("\n", "\r\n").replace("\r\r", "\r") +
        "BEGIN:VEVENT\r\n"
        "UID:entrevue@test.invalid\r\n"
        "SUMMARY:Conversation telephonique de 15 minutes\r\n"
        f"DTSTART{param}:{dtstart}\r\n"
        f"DTEND{param}:{dtend}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


@tagged("calendar_nextcloud_sync", "caldav_ics")
class TestIcsTimezoneWindows(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["nextcloud.calendar.sync.config"]

    # -- Le cas de l'incident, au caractère près ---------------------------

    def test_un_tzid_windows_est_resolu_par_le_vtimezone(self):
        """🔴 La régression à empêcher : douze heures d'écart.

        9 h le 2 septembre en Nouvelle-Zélande (UTC+12 en septembre), c'est
        21 h le 1er septembre en UTC. Le défaut rendait « 2026-09-02 09:00 ».
        """
        data = self.config._parse_ics_vevent(
            _ics(VTIMEZONE_NZ, "20260902T090000", "20260902T091500"))
        self.assertEqual(data["start"], "2026-09-01 21:00:00")
        self.assertEqual(data["end"], "2026-09-01 21:15:00")

    def test_l_heure_avancee_du_vtimezone_est_honoree(self):
        """Le VTIMEZONE porte AUSSI les bascules, et il faut les suivre.

        La Nouvelle-Zélande passe à +13 le dernier dimanche de septembre. Une
        table Windows→IANA statique ne dirait rien de cette date; le bloc du
        .ics, lui, la décrit. Mi-octobre, 9 h locale vaut donc 20 h la veille
        en UTC, et non 21 h.
        """
        data = self.config._parse_ics_vevent(
            _ics(VTIMEZONE_NZ, "20261015T090000", "20261015T091500"))
        self.assertEqual(data["start"], "2026-10-14 20:00:00")

    def test_sans_vtimezone_la_table_windows_prend_le_relais(self):
        """Deuxième ligne de défense : certains expéditeurs omettent le bloc."""
        data = self.config._parse_ics_vevent(
            _ics(None, "20260902T090000", "20260902T091500"))
        self.assertEqual(data["start"], "2026-09-01 21:00:00")

    # -- Ce qui ne doit PAS avoir changé -----------------------------------

    def test_un_tzid_iana_passe_toujours_par_tzdata(self):
        data = self.config._parse_ics_vevent(
            _ics(None, "20260902T090000", "20260902T091500", tzid="Pacific/Auckland"))
        self.assertEqual(data["start"], "2026-09-01 21:00:00")

    def test_une_heure_en_Z_reste_de_l_utc(self):
        data = self.config._parse_ics_vevent(
            _ics(None, "20260902T090000Z", "20260902T091500Z", tzid=None))
        self.assertEqual(data["start"], "2026-09-02 09:00:00")

    def test_montreal_n_a_pas_bouge(self):
        """Le fuseau de tous les jours ici : la garde qui prouve que le
        correctif n'a pas déplacé ce qui marchait déjà. 9 h à Montréal en
        septembre (EDT, UTC-4), c'est 13 h UTC."""
        data = self.config._parse_ics_vevent(
            _ics(None, "20260902T090000", "20260902T091500", tzid="America/Montreal"))
        self.assertEqual(data["start"], "2026-09-02 13:00:00")

    # -- Les défaillances qui ne doivent pas emporter la synchro -----------

    def test_un_vtimezone_malforme_n_emporte_pas_l_evenement(self):
        """Un bloc cassé chez un expéditeur ne doit pas tuer tout un agenda."""
        casse = "BEGIN:VTIMEZONE\nTZID:Bidon\nBEGIN:STANDARD\nDTSTART:pas-une-date\nEND:VTIMEZONE"
        data = self.config._parse_ics_vevent(
            _ics(casse, "20260902T090000", "20260902T091500", tzid="Bidon"))
        self.assertIsNotNone(data, "un VTIMEZONE illisible a fait perdre l'événement")
        self.assertEqual(data["uid"], "entrevue@test.invalid")

    def test_un_fuseau_totalement_inconnu_retombe_sur_utc(self):
        """Le repli subsiste — refuser l'événement serait pire — mais il est
        désormais journalisé en ERROR, pas en WARNING : un avertissement de
        cron ne se lit jamais, et c'est ce silence qui a laissé passer douze
        heures d'écart."""
        data = self.config._parse_ics_vevent(
            _ics(None, "20260902T090000", "20260902T091500", tzid="Fuseau Imaginaire"))
        self.assertEqual(data["start"], "2026-09-02 09:00:00")

    def test_les_fuseaux_du_ics_sont_indexes_par_le_tzid_brut(self):
        """Le bloc VTIMEZONE est indexé par le TZID tel qu'écrit, pas par son
        équivalent IANA : chercher avec le nom normalisé seul ne le trouve
        pas."""
        zones = self.config._ics_vtimezones(
            _ics(VTIMEZONE_NZ, "20260902T090000", "20260902T091500"))
        self.assertIn("New Zealand Standard Time", zones)
