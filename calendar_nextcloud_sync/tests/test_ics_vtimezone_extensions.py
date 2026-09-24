# -*- coding: utf-8 -*-
"""Propriétés X- dans les VTIMEZONE (2.17.0, relevé dans les journaux).

🔴 Suite directe de l'incident des fuseaux Windows
(`test_ics_timezone_windows.py`). Le correctif de fond était de lire les
VTIMEZONE embarqués dans le .ics plutôt que de maintenir une table
Windows→IANA. Sauf que `dateutil.tz.tzical` lève sur
toute propriété qu'il ne connaît pas, et Nextcloud écrit ``X-TZINFO`` dans
chacun de ses VTIMEZONE — Google écrit ``X-LIC-LOCATION``.

Ce que les journaux montraient, des centaines de fois par jour sur des bases
réelles :

    unsupported property: X-TZINFO
    unsupported property: X-LIC-LOCATION
    not enough values to unpack

Une seule de ces lignes faisait jeter la table VTIMEZONE **entière**, donc
retomber sur le repli qui prend l'heure murale locale pour de l'UTC : le même
décalage de douze heures que pour les fuseaux Windows, par une autre porte.

Or ces propriétés sont explicitement permises par la RFC 5545 §3.8.8.2.
Les ignorer est la bonne lecture, pas une tolérance.
"""

from odoo.tests import TransactionCase, tagged

TORONTO = """BEGIN:VTIMEZONE
TZID:America/Toronto
X-TZINFO:America/Toronto[2026a]
X-LIC-LOCATION:America/Toronto
BEGIN:STANDARD
DTSTART:16010101T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE"""

# TZOFFSETTO illisible : ce bloc doit être écarté SANS emporter les autres.
AVARIE = """BEGIN:VTIMEZONE
TZID:Broken/Zone
BEGIN:STANDARD
DTSTART:PASUNEDATE
TZOFFSETTO:NIMPORTEQUOI
END:STANDARD
END:VTIMEZONE"""

# Ligne repliée (RFC 5545 §3.1) + ligne sans séparateur « : ».
PARIS_PLIE = """BEGIN:VTIMEZONE
TZID:Europe/Pa
 ris
X-LIC-LOCATION:Europe/Paris
LIGNESANSDEUXPOINTS
BEGIN:STANDARD
DTSTART:16010101T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE"""


def _ics(*blocs):
    corps = "\r\n".join(b.replace("\n", "\r\n") for b in blocs)
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + corps + "\r\nEND:VCALENDAR\r\n"
    )


@tagged("post_install", "-at_install")
class TestIcsVtimezoneExtensions(TransactionCase):

    def setUp(self):
        super().setUp()
        self.cfg = self.env["nextcloud.calendar.sync.config"]

    def test_x_tzinfo_ne_jette_plus_le_fuseau(self):
        """Le cas de production : Nextcloud écrit X-TZINFO dans chaque bloc."""
        table = self.cfg._ics_vtimezones(_ics(TORONTO))
        self.assertIn(
            "America/Toronto", table,
            "X-TZINFO fait encore jeter la table : le repli UTC va décaler les "
            "rencontres de tout le fuseau.",
        )

    def test_offsets_avec_heure_avancee(self):
        """Le fuseau lu doit honorer la bascule, pas rendre un offset figé."""
        import datetime
        tz = self.cfg._ics_vtimezones(_ics(TORONTO))["America/Toronto"]
        self.assertEqual(
            datetime.datetime(2026, 1, 15, 12, tzinfo=tz).utcoffset(),
            datetime.timedelta(hours=-5), "janvier doit être à -05:00")
        self.assertEqual(
            datetime.datetime(2026, 7, 15, 12, tzinfo=tz).utcoffset(),
            datetime.timedelta(hours=-4), "juillet doit être à -04:00")

    def test_un_bloc_avarie_n_emporte_pas_les_autres(self):
        """C'est tout l'intérêt de la reprise bloc par bloc."""
        table = self.cfg._ics_vtimezones(_ics(TORONTO, AVARIE))
        self.assertIn("America/Toronto", table)
        self.assertNotIn("Broken/Zone", table)

    def test_ligne_repliee_et_ligne_sans_separateur(self):
        table = self.cfg._ics_vtimezones(_ics(PARIS_PLIE))
        self.assertIn(
            "Europe/Paris", table,
            "le dépliage RFC 5545 doit recoller « Europe/Pa » + « ris »")

    def test_entrees_vides_ne_levent_pas(self):
        self.assertEqual(self.cfg._ics_vtimezones(""), {})
        self.assertEqual(self.cfg._ics_vtimezones(None), {})
        self.assertEqual(
            self.cfg._ics_vtimezones("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"), {})
