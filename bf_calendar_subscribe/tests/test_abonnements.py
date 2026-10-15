# -*- coding: utf-8 -*-
"""Agendas externes abonnés (lot 6)."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytz

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_calendar_subscribe.models import fetch, subscription

LIRE = "odoo.addons.bf_calendar_subscribe.models.fetch.lire"

OUTLOOK = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:Microsoft Exchange Server 2010
METHOD:PUBLISH
X-WR-CALNAME:Agenda CLIENT
BEGIN:VTIMEZONE
TZID:Eastern Standard Time
BEGIN:STANDARD
DTSTART:16010101T020000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=1SU;BYMONTH=11
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=2SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:hebdo-client
SUMMARY:Comité CLIENT
DTSTART;TZID=Eastern Standard Time:20261019T100000
DTEND;TZID=Eastern Standard Time:20261019T110000
RRULE:FREQ=WEEKLY;COUNT=4
EXDATE;TZID=Eastern Standard Time:20261102T100000
LOCATION:Salle du conseil
END:VEVENT
BEGIN:VEVENT
UID:hebdo-client
RECURRENCE-ID;TZID=Eastern Standard Time:20261026T100000
SUMMARY:Comité CLIENT (déplacé)
DTSTART;TZID=Eastern Standard Time:20261027T140000
DTEND;TZID=Eastern Standard Time:20261027T150000
END:VEVENT
BEGIN:VEVENT
UID:conge-client
SUMMARY:Congé
DTSTART;VALUE=DATE:20261030
DTEND;VALUE=DATE:20261101
END:VEVENT
BEGIN:VEVENT
UID:libre-client
SUMMARY:Disponible
TRANSP:TRANSPARENT
DTSTART:20261021T150000Z
DTEND:20261021T160000Z
END:VEVENT
BEGIN:VEVENT
UID:annule-client
SUMMARY:Annulée
STATUS:CANCELLED
DTSTART:20261022T150000Z
DTEND:20261022T160000Z
END:VEVENT
END:VCALENDAR
"""


@tagged("post_install", "-at_install", "bf_calendar_subscribe")
class TestAbonnements(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_mail_to_attendees=True, skip_nc_sync=True))
        cls.proprio = new_test_user(cls.env, login="proprio_abo", groups="base.group_user",
                                    tz="America/Toronto")
        cls.collegue = new_test_user(cls.env, login="collegue_abo", groups="base.group_user")
        cls.Sub = cls.env["bf.calendar.subscription"].with_user(cls.proprio)

    def _abonnement(self, **vals):
        return self.Sub.create(dict({"name": "CLIENT (Outlook)",
                                     "url": "webcal://outlook.example.com/owa/calendar/x/calendar.ics",
                                     "past_days": 0, "future_days": 400}, **vals))

    def _synchroniser(self, sub, texte=OUTLOOK, statut=200):
        with patch(LIRE, return_value=(statut, {"ETag": '"e1"'}, texte.encode())), \
                patch("odoo.fields.Datetime.now", return_value=datetime(2026, 10, 15, 0, 0)):
            sub._bf_synchroniser(force=True)
        return self.env["calendar.event"].sudo().search(
            [("bf_subscription_id", "=", sub.id)], order="start")

    # -- lecture du fichier ---------------------------------------------------

    def test_outlook_fuseau_windows_exdate_exception_journee(self):
        tz = pytz.timezone("America/Toronto")
        lues, ignores, tronque = subscription.instances(
            OUTLOOK, datetime(2026, 10, 15), datetime(2026, 12, 31), tz)
        self.assertEqual((ignores, tronque), (0, False))
        resumes = sorted((l["summary"], l["journee"]) for l in lues)
        self.assertEqual(resumes, [("Comité CLIENT", False), ("Comité CLIENT", False),
                                   ("Comité CLIENT (déplacé)", False), ("Congé", True),
                                   ("Disponible", False)])
        premiere = next(l for l in lues if l["summary"] == "Comité CLIENT")
        self.assertEqual(premiere["start"], datetime(2026, 10, 19, 14, 0))  # 10 h EDT = 14 h UTC
        conge = next(l for l in lues if l["summary"] == "Congé")
        self.assertEqual((conge["start"], conge["stop"]), (date(2026, 10, 30), date(2026, 10, 31)))
        novembre = [l for l in lues if l["summary"] == "Comité CLIENT" and l["start"].month == 11]
        self.assertEqual([l["start"] for l in novembre], [datetime(2026, 11, 9, 15, 0)])  # 10 h EST

    # -- synchronisation -----------------------------------------------------

    def test_import_occupe_prive_lecture_seule(self):
        sub = self._abonnement()
        evenements = self._synchroniser(sub)
        self.assertEqual(sub.state, "ok")
        self.assertEqual(len(evenements), 4)  # sans l'événement libre ni l'annulé
        self.assertEqual(set(evenements.mapped("privacy")), {"private"})
        self.assertEqual(set(evenements.mapped("show_as")), {"busy"})
        self.assertEqual(set(evenements.mapped("user_id").ids), {self.proprio.id})
        self.assertFalse(any(evenements.mapped("location")))
        self.assertFalse(evenements.mapped("alarm_ids"))
        with self.assertRaises(UserError):
            evenements[0].with_user(self.proprio).write({"name": "Changé"})
        with self.assertRaises(UserError):
            evenements[0].with_user(self.proprio).unlink()

    def test_resynchroniser_ne_reecrit_rien_et_retire_les_disparus(self):
        sub = self._abonnement()
        avant = self._synchroniser(sub)
        Event = type(self.env["calendar.event"])
        with patch.object(Event, "write", autospec=True, side_effect=Event.write) as ecritures:
            apres = self._synchroniser(sub)
        self.assertEqual(avant.ids, apres.ids)
        self.assertFalse(ecritures.call_count, "une relecture identique ne doit rien réécrire")
        sans_conge = OUTLOOK.replace("UID:conge-client", "UID:conge-client-x").replace(
            "DTSTART;VALUE=DATE:20261030", "DTSTART;VALUE=DATE:20250101").replace(
            "DTEND;VALUE=DATE:20261101", "DTEND;VALUE=DATE:20250102")
        restants = self._synchroniser(sub, sans_conge)
        self.assertNotIn("Congé", restants.mapped("name"))

    def test_options_libres_et_lieux(self):
        sub = self._abonnement(import_free=True, import_location=True)
        evenements = self._synchroniser(sub)
        self.assertIn("Disponible", evenements.mapped("name"))
        self.assertIn("Salle du conseil", evenements.mapped("location"))

    def test_collegue_ne_voit_que_occupe(self):
        sub = self._abonnement()
        evenement = self._synchroniser(sub)[0]
        self.env.invalidate_all()
        self.assertNotEqual(evenement.with_user(self.collegue).read(["name"])[0]["name"], "Comité CLIENT")

    def test_erreur_gardee_sans_rien_casser(self):
        sub = self._abonnement()
        self._synchroniser(sub)
        self._synchroniser(sub, "<html>pas un agenda</html>")
        self.assertEqual(sub.state, "error")
        self.assertEqual(self.env["calendar.event"].sudo().search_count(
            [("bf_subscription_id", "=", sub.id)]), 4)

    def test_304_ne_touche_rien(self):
        sub = self._abonnement()
        self._synchroniser(sub)
        with patch(LIRE, return_value=(304, {}, b"")):
            sub._bf_synchroniser()
        self.assertEqual(sub.state, "ok")

    def test_supprimer_l_abonnement_retire_ses_evenements(self):
        sub = self._abonnement()
        self._synchroniser(sub)
        sub.unlink()
        self.assertFalse(self.env["calendar.event"].sudo().search_count([("bf_subscription_key", "like", "hebdo-client%")]))

    def test_adresse_invisible_aux_collegues(self):
        sub = self._abonnement()
        self.assertFalse(self.env["bf.calendar.subscription"].with_user(self.collegue).search([("id", "=", sub.id)]))

    # -- garde -----------------------------------------------------------------

    def test_garde_schemas(self):
        self.assertEqual(fetch.normaliser("webcal://a.example.com/x.ics"), "https://a.example.com/x.ics")
        for mauvaise in ("http://a.example.com/x.ics", "file:///etc/passwd", "ftp://a/x", ""):
            with self.assertRaises(fetch.LectureRefusee):
                fetch.normaliser(mauvaise)
        with self.assertRaises(UserError):
            self._abonnement(url="http://outlook.example.com/x.ics")

    def test_garde_adresses_internes(self):
        for hote in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "localhost", "[::1]"):
            with self.assertRaises(fetch.LectureRefusee):
                fetch.lire("https://%s/calendar.ics" % hote)
        with self.assertRaises(fetch.LectureRefusee):
            fetch.lire("https://example.com:8443/calendar.ics")

    # -- la promesse : les créneaux occupés ferment la prise de rendez-vous ----

    def test_creneau_importe_ferme_la_prise_de_rendez_vous(self):
        Calendar = self.env["resource.calendar"]
        if not hasattr(Calendar, "_calendar_event_busy_intervals"):
            self.skipTest("resource_booking absent")
        sub = self._abonnement()
        self._synchroniser(sub)
        ressource = self.env["resource.resource"].create({
            "name": "Proprio", "resource_type": "user", "user_id": self.proprio.id})
        debut = pytz.utc.localize(datetime(2026, 10, 19, 13, 0))
        fin = pytz.utc.localize(datetime(2026, 10, 19, 17, 0))
        # Les appelants réels passent la réservation analysée (jamais False, qui
        # sauterait tout événement sans réservation) : une réservation fictive.
        occupes = list(Calendar.sudo()._calendar_event_busy_intervals(debut, fin, ressource, -1))
        self.assertTrue(any(i[0] <= pytz.utc.localize(datetime(2026, 10, 19, 14, 30)) < i[1]
                            for i in occupes), occupes)

    # -- relecture adverse du lot 6 ---------------------------------------------

    def test_b1_jamais_rattache_a_nextcloud(self):
        if "x_nc_calendar_id" not in self.env["calendar.event"]._fields:
            self.skipTest("calendar_nextcloud_sync absent")
        # Un agenda Nextcloud par défaut RÉEL, comme en prod (les bases neutralisées ont 0).
        config = self.env["nextcloud.calendar.sync.config"].create({
            "name": "Défaut", "backend_type": "nextcloud", "nextcloud_base_url": "https://nc.example.test",
            "caldav_path": "/remote.php/dav/calendars/o/bf/", "nextcloud_user": "o",
            "nextcloud_app_password": "x", "sync_direction": "both"})
        self.env["ir.config_parameter"].sudo().set_param(
            "calendar_nextcloud_sync.default_calendar_id", str(config.id))
        evenements = self._synchroniser(self._abonnement())
        self.assertFalse(evenements.mapped("x_nc_calendar_id"))
        self.assertFalse(any(evenements.mapped("x_nc_uid")))

    def test_m1_proprietaire_verrouille(self):
        sub = self._abonnement()
        with self.assertRaises(UserError):
            sub.write({"owner_id": self.collegue.id})

    def test_verrou_sans_cle_de_contexte(self):
        evenement = self._synchroniser(self._abonnement())[0]
        with self.assertRaises(UserError):
            evenement.with_user(self.proprio).with_context(bf_subscription_sync=True).write({"name": "X"})
        with self.assertRaises(UserError):
            evenement.with_user(self.proprio).write({"categ_ids": [(5, 0, 0)]})

    def test_m4_exception_deplacee_sur_l_occurrence_suivante(self):
        texte = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:quotidien
SUMMARY:Quotidien
DTSTART:20261020T140000Z
DTEND:20261020T150000Z
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:quotidien
RECURRENCE-ID:20261020T140000Z
SUMMARY:Quotidien (déplacé)
DTSTART:20261021T140000Z
DTEND:20261021T150000Z
END:VEVENT
END:VCALENDAR
"""
        sub = self._abonnement()
        premiers = self._synchroniser(sub, texte)
        self.assertEqual(len(premiers), 3)
        self.assertEqual(len(set(premiers.mapped("bf_subscription_key"))), 3)
        seconds = self._synchroniser(sub, texte)
        self.assertEqual(premiers.ids, seconds.ids)

    def test_m5_un_vevent_fautif_ne_gele_pas_les_autres(self):
        texte = OUTLOOK.replace("UID:libre-client", "UID:fautif\nDTEND:20200101T000000Z\nX-A:1\nEND:VEVENT\nBEGIN:VEVENT\nUID:libre-client")
        texte = texte.replace("BEGIN:VEVENT\nUID:annule-client", "BEGIN:VEVENT\nUID:sans-debut\nRECURRENCE-ID:20261020T140000Z\nSUMMARY:x\nEND:VEVENT\nBEGIN:VEVENT\nUID:annule-client")
        sub = self._abonnement()
        evenements = self._synchroniser(sub, texte)
        self.assertEqual(sub.state, "ok")
        self.assertIn("Congé", evenements.mapped("name"))

    def test_vcard_refuse_sans_rien_effacer(self):
        sub = self._abonnement()
        self._synchroniser(sub)
        self._synchroniser(sub, "BEGIN:VCARD\nVERSION:3.0\nFN:X\nEND:VCARD\n")
        self.assertEqual(sub.state, "error")
        self.assertEqual(self.env["calendar.event"].sudo().search_count([("bf_subscription_id", "=", sub.id)]), 4)

    def test_repetition_a_la_minute_refusee(self):
        texte = OUTLOOK.replace("RRULE:FREQ=WEEKLY;COUNT=4", "RRULE:FREQ=MINUTELY")
        tz = pytz.timezone("America/Toronto")
        lues, ignores, _t = subscription.instances(texte, datetime(2026, 10, 15), datetime(2026, 12, 31), tz)
        self.assertEqual(ignores, 1)
        self.assertNotIn("Comité CLIENT", [l["summary"] for l in lues])

    def test_option_changee_relit_meme_en_304(self):
        sub = self._abonnement()
        self._synchroniser(sub)
        sub.write({"privacy": "public"})
        self.assertFalse(sub.http_etag)

    def test_304_garde_les_evenements(self):
        sub = self._abonnement()
        avant = self._synchroniser(sub)
        with patch(LIRE, return_value=(304, {}, b"")):
            sub._bf_synchroniser()
        self.assertEqual(self.env["calendar.event"].sudo().search(
            [("bf_subscription_id", "=", sub.id)], order="start").ids, avant.ids)

    def test_garde_propre_hote_par_nom_et_par_ip(self):
        with self.assertRaisesRegex(fetch.LectureRefusee, "ce serveur"):
            fetch.lire("https://odoo.example.com/calendar/share/x.ics", hotes_interdits=["odoo.example.com"])
        infos = [(2, 1, 6, "", ("8.8.8.8", 443))]
        with patch("socket.getaddrinfo", return_value=infos):
            with self.assertRaisesRegex(fetch.LectureRefusee, "ce serveur"):
                fetch.lire("https://autre-domaine.example.com/x.ics", ips_interdites={"8.8.8.8"})

    def test_garde_nom_qui_resout_en_prive(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.1.2.3", 443))]):
            with self.assertRaisesRegex(fetch.LectureRefusee, "non publique"):
                fetch.lire("https://public-en-apparence.example.com/x.ics")

    def test_b_boucle_entre_symbifox_coupee(self):
        texte = OUTLOOK.replace("UID:conge-client", "UID:conge-client\nX-SYMBIFOX-IMPORTED:1")
        tz = pytz.timezone("America/Toronto")
        lues, _i, _t = subscription.instances(texte, datetime(2026, 10, 15), datetime(2026, 12, 31), tz)
        self.assertNotIn("Congé", [l["summary"] for l in lues])

    def test_a_regles_trop_fines_refusees(self):
        tz = pytz.timezone("America/Toronto")
        for regle in ("RRULE:FREQ=HOURLY;BYMINUTE=0,30", "RRULE:FREQ=WEEKLY;COUNT=4\nEXRULE:FREQ=DAILY",
                      "RRULE:FREQ=WEEKLY;COUNT=4\nRRULE:FREQ=SECONDLY"):
            texte = OUTLOOK.replace("RRULE:FREQ=WEEKLY;COUNT=4", regle)
            lues, ignores, _t = subscription.instances(texte, datetime(2026, 10, 15), datetime(2026, 12, 31), tz)
            self.assertEqual(ignores, 1, regle)

    def test_a_echeance_de_depliage(self):
        tz = pytz.timezone("America/Toronto")
        with patch.object(subscription, "DUREE_DEPLIAGE", -1):
            lues, _i, tronque = subscription.instances(
                OUTLOOK, datetime(2026, 10, 15), datetime(2026, 12, 31), tz)
        self.assertTrue(tronque)
        self.assertFalse(lues)

    def test_a_cron_reserve_avant_de_lire(self):
        sub = self._abonnement()
        with patch.object(type(sub), "_bf_synchroniser", side_effect=RuntimeError("tué")):
            self.env["bf.calendar.subscription"]._cron_bf_synchroniser()
        self.assertTrue(sub.last_fetch)

    def test_archive_ne_ferme_plus_rien(self):
        sub = self._abonnement()
        self._synchroniser(sub)
        sub.write({"active": False})
        self.assertFalse(self.env["calendar.event"].sudo().search_count([("bf_subscription_id", "=", sub.id)]))


    def test_serie_marquee(self):
        evenements = self._synchroniser(self._abonnement())
        self.assertTrue(all(e.bf_subscription_serie for e in evenements if e.name.startswith("Comité")))
        self.assertFalse(evenements.filtered(lambda e: e.name == "Congé").bf_subscription_serie)
