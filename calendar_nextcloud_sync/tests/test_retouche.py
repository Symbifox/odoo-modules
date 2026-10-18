# -*- coding: utf-8 -*-
"""Ce qu'Odoo modifie sur un événement déjà dans Nextcloud y repart (2.25.0).

Le cas d'origine, le 2026-10-10 : la statutaire du lundi
2026-10-12, occurrence d'une série née dans Nextcloud, déplacée au mardi dans
Odoo. Nextcloud l'a gardée au lundi, et une plage née dans Odoo puis glissée
d'une demi-heure y est restée à l'ancienne heure.

Le serveur CalDAV est simulé par un petit magasin en mémoire : GET rend la
ressource et son ETag, PUT la remplace (en vérifiant If-Match), comme Sabre.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.calendar_nextcloud_sync.models import caldav_backend as cb

REQUESTS = "odoo.addons.calendar_nextcloud_sync.models.caldav_backend.requests"

VTZ = """BEGIN:VTIMEZONE
TZID:America/Toronto
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:EDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""

SIMPLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Sabre//Sabre VObject 4.5.6//EN
%s
BEGIN:VEVENT
UID:simple-essai
DTSTAMP:20261010T214200Z
CREATED:20261010T214200Z
SUMMARY:RÉSERVÉ si Alex souhaite déplacer statutaire
DTSTART;TZID=America/Toronto:20261013T160000
DTEND;TZID=America/Toronto:20261013T170000
STATUS:TENTATIVE
SEQUENCE:2
X-MOZ-GENERATION:3
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
DESCRIPTION:Rappel posé au téléphone
END:VALARM
END:VEVENT
END:VCALENDAR
""" % VTZ

SERIE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Sabre//Sabre VObject 4.5.6//EN
%s
BEGIN:VEVENT
UID:serie-essai
DTSTAMP:20260914T204900Z
SUMMARY:Rencontre statutaire
DTSTART;TZID=America/Toronto:20261005T160000
DTEND;TZID=America/Toronto:20261005T170000
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=6
ATTENDEE;CN=Alex:mailto:alex.essai@example.invalid
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT10M
DESCRIPTION:Rappel
END:VALARM
END:VEVENT
END:VCALENDAR
""" % VTZ


class Magasin:
    """Une collection CalDAV en mémoire, au comportement de Sabre."""

    def __init__(self, ressources):
        self.ressources = dict(ressources)
        self.version = 0
        self.etags = {url: '"v0"' for url in self.ressources}
        self.puts = []
        self.refus_412 = 0

    def get(self, url, **kwargs):
        if url not in self.ressources:
            return SimpleNamespace(status_code=404, headers={}, text="",
                                   raise_for_status=lambda: None, encoding=None)
        return SimpleNamespace(status_code=200, headers={"ETag": self.etags[url]},
                               text=self.ressources[url],
                               raise_for_status=lambda: None, encoding=None)

    def put(self, url, data=None, headers=None, **kwargs):
        headers = headers or {}
        if self.refus_412:
            self.refus_412 -= 1
            self.version += 1
            self.etags[url] = '"v%d"' % self.version
            return SimpleNamespace(status_code=412, headers={},
                                   raise_for_status=lambda: None)
        if "If-Match" in headers and headers["If-Match"] != self.etags.get(url):
            return SimpleNamespace(status_code=412, headers={},
                                   raise_for_status=lambda: None)
        self.puts.append((url, data.decode("utf-8"), headers))
        self.ressources[url] = data.decode("utf-8")
        self.version += 1
        self.etags[url] = '"v%d"' % self.version
        return SimpleNamespace(status_code=204, headers={"ETag": self.etags[url]},
                               raise_for_status=lambda: None)

    def delete(self, url, **kwargs):
        self.ressources.pop(url, None)
        return SimpleNamespace(status_code=204, headers={}, raise_for_status=lambda: None)


def _vevents(texte):
    lignes = cb.deplier(texte)
    return [lignes[d:f + 1] for d, f in cb.blocs_vevent(lignes)]


def _prop(bloc, nom):
    return [ligne for _i, ligne in cb.props_de_tete(bloc) if cb.nom_prop(ligne) == nom]


class BaseRetouche(TransactionCase):
    """Configuration, magasin CalDAV simulé et outils communs (aucun essai ici)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, no_mail_to_attendees=True))
        cls.config = cls.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda essai",
            "backend_type": "nextcloud",
            "nextcloud_base_url": "https://nc.example.test",
            "caldav_path": "/remote.php/dav/calendars/alice/perso/",
            "nextcloud_user": "alice",
            "nextcloud_app_password": "mdp-essai",
            "sync_direction": "both",
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "calendar_nextcloud_sync.push_via_caldav", "1")
        cls.backend = cls.env["calendar.caldav.backend"]
        cls.url_simple = "https://nc.example.test/remote.php/dav/calendars/alice/perso/simple-essai.ics"
        cls.url_serie = "https://nc.example.test/remote.php/dav/calendars/alice/perso/serie-essai.ics"

    # -- outils -----------------------------------------------------------

    def _magasin(self, **ressources):
        magasin = Magasin(ressources)
        patches = [
            patch(REQUESTS + ".get", side_effect=magasin.get),
            patch(REQUESTS + ".put", side_effect=magasin.put),
            patch(REQUESTS + ".delete", side_effect=magasin.delete),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return magasin

    def _simple_nextcloud(self):
        """L'événement tel que la lecture le laisse : né ailleurs, marqué nextcloud.

        ⚠️ Rendu sans `skip_nc_sync` : un enregistrement garde le contexte de
        sa création, et toutes les écritures de l'essai seraient restées muettes.
        """
        event = self.env["calendar.event"].with_context(skip_nc_sync=True).create({
            "name": "RÉSERVÉ si Alex souhaite déplacer statutaire",
            "start": datetime(2026, 10, 13, 20, 0),
            "stop": datetime(2026, 10, 13, 21, 0),
            "x_nc_uid": "simple-essai",
            "x_nc_calendar_id": self.config.id,
            "x_sync_source": "nextcloud",
            "x_caldav_href": "/remote.php/dav/calendars/alice/perso/simple-essai.ics",
            "x_caldav_etag": "v0",
        })
        return self.env["calendar.event"].browse(event.id)

    def _donnees_serie(self, **autres):
        data = {
            "uid": "serie-essai",
            "summary": "Rencontre statutaire",
            "start": "2026-10-05 20:00:00",
            "end": "2026-10-05 21:00:00",
            "allday": False,
            "location": "",
            "description": "",
            "attendees": [],
            "rrule": "FREQ=WEEKLY;BYDAY=MO;COUNT=6",
            "exdates": [],
            "event_tz": "America/Toronto",
            "alarms": None,
            "status": None,
            "etag": "v0",
            "href": "/remote.php/dav/calendars/alice/perso/serie-essai.ics",
        }
        data.update(autres)
        return data

    def _serie(self, **autres):
        res = self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(**autres), self.config.id)
        base = self.env["calendar.event"].browse(res["id"])
        return base, base.recurrence_id.calendar_event_ids.sorted("start")



@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestRetoucheNextcloud(BaseRetouche):

    # -- retouche d'un événement simple -------------------------------------

    def test_retouche_garde_ce_qu_odoo_ne_connait_pas(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        event.write({"start": datetime(2026, 10, 13, 19, 30),
                     "stop": datetime(2026, 10, 13, 20, 30)})
        self.assertEqual(len(magasin.puts), 1)
        _url, corps, en_tetes = magasin.puts[0]
        self.assertEqual(en_tetes.get("If-Match"), '"v0"')
        self.assertEqual(en_tetes.get("x-nc-scheduling"), "false")
        (bloc,) = _vevents(corps)
        self.assertEqual(_prop(bloc, "DTSTART"),
                         ["DTSTART;TZID=America/Toronto:20261013T153000"])
        self.assertEqual(_prop(bloc, "DTEND"),
                         ["DTEND;TZID=America/Toronto:20261013T163000"])
        self.assertEqual(_prop(bloc, "SEQUENCE"), ["SEQUENCE:3"])
        # Ce qu'Odoo ne modélise pas reste là.
        self.assertIn("X-MOZ-GENERATION:3", corps)
        self.assertIn("CREATED:20261010T214200Z", corps)
        self.assertIn("DESCRIPTION:Rappel posé au téléphone", corps)
        self.assertEqual(event.x_caldav_etag, "v1")

    def test_ecriture_sans_changement_ne_reecrit_rien(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        event.with_context(skip_nc_sync=True).write({"start": datetime(2026, 10, 13, 20, 0)})
        # Même heure, même titre : la ressource ne change pas.
        event.write({"name": event.name})
        self.assertFalse(magasin.puts)

    def test_412_relit_et_rejoue_une_fois(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        magasin.refus_412 = 1
        event = self._simple_nextcloud()
        event.write({"name": "Statutaire déplacée"})
        self.assertEqual(len(magasin.puts), 1)
        self.assertIn("SUMMARY:Statutaire déplacée", magasin.puts[0][1])

    def test_echec_ne_bloque_pas_odoo_et_se_dit(self):
        self._magasin()  # la ressource n'existe pas : GET 404
        event = self._simple_nextcloud()
        with patch.object(type(self.env.user), "_bus_send") as avis:
            event.write({"name": "Renommée"})
        self.assertEqual(event.name, "Renommée")
        self.assertEqual(self.config.last_sync_status, "error")
        self.assertTrue(avis.called)

    def test_evenement_odoo_garde_la_poussee_complete(self):
        """Un événement encore « odoo » passe par push(), pas par la retouche."""
        magasin = self._magasin()
        event = self._simple_nextcloud()
        event.with_context(skip_nc_sync=True).write({"x_sync_source": "odoo"})
        with patch.object(type(self.backend), "_retoucher_evenement") as retouche:
            event.write({"name": "Toujours poussée en entier"})
        retouche.assert_not_called()
        self.assertEqual(len(magasin.puts), 1)

    def test_statut_seul_repart(self):
        if "bf_event_status" not in self.env["calendar.event"]._fields:
            self.skipTest("bf_calendar_invite absent")
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        event.write({"bf_event_status": "cancelled"})
        self.assertIn("STATUS:CANCELLED", magasin.puts[-1][1])

    # -- lecture d'une série et de ses exceptions -----------------------------

    def test_lecture_rend_le_maitre_et_ses_exceptions(self):
        exception = """BEGIN:VEVENT
UID:serie-essai
RECURRENCE-ID;TZID=America/Toronto:20261012T160000
DTSTART;TZID=America/Toronto:20261013T153000
DTEND;TZID=America/Toronto:20261013T163000
SUMMARY:Rencontre statutaire
END:VEVENT
"""
        # L'exception AVANT le maître : avant la 2.25, elle passait pour la série.
        texte = SERIE.replace("BEGIN:VEVENT", exception + "BEGIN:VEVENT", 1)
        data = self.config._parse_ics_vevent(texte)
        self.assertEqual(data["rrule"], "FREQ=WEEKLY;BYDAY=MO;COUNT=6")
        self.assertEqual(data["start"], "2026-10-05 20:00:00")
        (ov,) = data["overrides"]
        self.assertEqual(ov["recurrence_id"], datetime(2026, 10, 12, 20, 0))
        self.assertEqual(ov["start"], "2026-10-13 19:30:00")

    def test_serie_inchangee_garde_ses_occurrences(self):
        base, occurrences = self._serie()
        self.assertEqual(len(occurrences), 6)
        self.assertTrue(all(occurrences.mapped("x_nc_recurrence_id")))
        ids = occurrences.ids
        # La même série revient (notre propre écho, ou un passage complet).
        res = self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v1"), self.config.id)
        self.assertEqual(res["action"], "updated")
        self.assertEqual(sorted(base.recurrence_id.calendar_event_ids.ids), sorted(ids))
        self.assertTrue(all(self.env["calendar.event"].browse(ids).exists()))
        self.assertEqual(base.x_caldav_etag, "v1")

    def test_exception_lue_deplace_l_occurrence_sans_changer_son_id(self):
        base, occurrences = self._serie()
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        self.assertEqual(len(lundi), 1)
        exception = {
            "uid": "serie-essai", "summary": "Rencontre statutaire",
            "start": "2026-10-13 19:30:00", "end": "2026-10-13 20:30:00",
            "allday": False, "recurrence_id": datetime(2026, 10, 12, 20, 0),
            "recurrence_id_allday": False, "status": None,
        }
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v2", overrides=[exception]), self.config.id)
        self.assertTrue(lundi.exists())
        self.assertEqual(lundi.start, datetime(2026, 10, 13, 19, 30))
        self.assertEqual(lundi.x_nc_recurrence_id, datetime(2026, 10, 12, 20, 0))

    def test_serie_changee_est_recreee_comme_avant(self):
        _base, occurrences = self._serie()
        ids = occurrences.ids
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(summary="Statutaire renommée", etag="v3"), self.config.id)
        self.assertFalse(self.env["calendar.event"].browse(ids).exists())

    # -- occurrence déplacée, annulée ou supprimée dans Odoo ------------------

    def test_occurrence_deplacee_devient_une_exception(self):
        magasin = self._magasin(**{self.url_serie: SERIE})
        base, occurrences = self._serie()
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        lundi.write({"start": datetime(2026, 10, 13, 19, 30),
                     "stop": datetime(2026, 10, 13, 20, 30),
                     "recurrence_update": "self_only"})
        self.assertEqual(len(magasin.puts), 1)
        maitre, exception = _vevents(magasin.puts[0][1])
        # Le maître est intact : sa règle, ses participants, son alarme.
        self.assertEqual(_prop(maitre, "RRULE"), ["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=6"])
        self.assertEqual(_prop(maitre, "DTSTART"),
                         ["DTSTART;TZID=America/Toronto:20261005T160000"])
        # L'exception désigne le lundi d'origine et porte le mardi.
        self.assertEqual(_prop(exception, "RECURRENCE-ID"),
                         ["RECURRENCE-ID;TZID=America/Toronto:20261012T160000"])
        self.assertEqual(_prop(exception, "DTSTART"),
                         ["DTSTART;TZID=America/Toronto:20261013T153000"])
        self.assertFalse(_prop(exception, "RRULE"))
        self.assertTrue(_prop(exception, "ATTENDEE"))
        self.assertIn("BEGIN:VALARM", "\n".join(exception))
        self.assertEqual(lundi.x_nc_recurrence_id, datetime(2026, 10, 12, 20, 0))
        self.assertEqual(base.x_caldav_etag, "v1")

        # Déplacée une seconde fois : la même exception est mise à jour.
        lundi.write({"start": datetime(2026, 10, 13, 20, 0),
                     "stop": datetime(2026, 10, 13, 21, 0),
                     "recurrence_update": "self_only"})
        vevents = _vevents(magasin.ressources[self.url_serie])
        self.assertEqual(len(vevents), 2)
        self.assertEqual(_prop(vevents[1], "DTSTART"),
                         ["DTSTART;TZID=America/Toronto:20261013T160000"])

    def test_reecrire_toute_la_serie_ne_part_pas(self):
        magasin = self._magasin(**{self.url_serie: SERIE})
        _base, occurrences = self._serie()
        occurrences[2].write({"name": "Toute la série", "recurrence_update": "all_events"})
        self.assertFalse(magasin.puts)

    def test_occurrence_supprimee_devient_un_exdate(self):
        magasin = self._magasin(**{self.url_serie: SERIE})
        _base, occurrences = self._serie()
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        lundi.unlink()
        (maitre,) = _vevents(magasin.ressources[self.url_serie])
        self.assertEqual(_prop(maitre, "EXDATE"),
                         ["EXDATE;TZID=America/Toronto:20261012T160000"])

    def test_porteur_supprime_passe_son_adresse(self):
        self._magasin(**{self.url_serie: SERIE})
        base, occurrences = self._serie()
        reste = occurrences - base
        base.unlink()
        heritier = reste.filtered("x_caldav_href")
        self.assertEqual(len(heritier), 1)
        self.assertEqual(heritier.x_nc_uid, "serie-essai")

    def test_occurrence_detachee_avant_la_2_25_retrouve_son_origine(self):
        """Le cas réel : déplacée dans Odoo avant la 2.25, sans heure d'origine gardée."""
        _base, occurrences = self._serie()
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        lundi.with_context(skip_nc_sync=True, tracking_disable=False).write({
            "start": datetime(2026, 10, 13, 19, 30),
            "stop": datetime(2026, 10, 13, 20, 30),
            "recurrence_update": "self_only",
        })
        # Le suivi des modifications s'écrit au pré-commit, que l'essai n'atteint pas.
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.cr.execute(
            "UPDATE calendar_event SET x_nc_recurrence_id = NULL WHERE id = %s", (lundi.id,))
        lundi.invalidate_recordset(["x_nc_recurrence_id"])
        self.assertFalse(lundi.follow_recurrence)
        self.assertEqual(lundi._bf_nc_origine(), datetime(2026, 10, 12, 20, 0))


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestRetoucheRelecture(BaseRetouche):
    """Les cas limites de la retouche, un essai par cas."""

    def _reponse_sync(self, etag, ics):
        corps = (
            '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response>'
            "<d:href>/remote.php/dav/calendars/alice/perso/simple-essai.ics</d:href>"
            "<d:propstat><d:prop><d:getetag>\"%s\"</d:getetag>"
            "<c:calendar-data>%s</c:calendar-data></d:prop>"
            "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            "<d:sync-token>jeton-2</d:sync-token></d:multistatus>"
        ) % (etag, ics.replace("&", "&amp;").replace("<", "&lt;"))
        return SimpleNamespace(status_code=207, text=corps, headers={})

    def _lecture_incrementale(self, etag, ics):
        self.config.caldav_sync_token = "jeton-1"
        cible = "odoo.addons.calendar_nextcloud_sync.models.nextcloud_sync_config.requests.request"
        with patch(cible, return_value=self._reponse_sync(etag, ics)):
            self.config._pull_incremental()

    def test_b1_l_echo_de_notre_ecriture_est_saute(self):
        event = self._simple_nextcloud()
        invite = self.env["res.partner"].create({"name": "Ajoutée dans Odoo"})
        event.with_context(skip_nc_sync=True).write({
            "partner_ids": [(4, invite.id)], "x_caldav_etag": "v5"})
        self._lecture_incrementale("v5", SIMPLE)
        self.assertIn(invite, event.partner_ids)
        # Un ETag neuf, lui, est une vraie modification venue de Nextcloud.
        self._lecture_incrementale("v6", SIMPLE)
        self.assertNotIn(invite, event.partner_ids)

    def test_b2_dates_utc_naives(self):
        dt, jour = self.config._parse_ics_datetime("20261012T200000Z", "")
        self.assertIsNone(dt.tzinfo)
        self.assertEqual(dt, datetime(2026, 10, 12, 20, 0))
        base, occurrences = self._serie()
        lundi = occurrences.filtered(lambda e: e.start == datetime(2026, 10, 12, 20, 0))
        exception = {"uid": "serie-essai", "summary": "Rencontre statutaire",
                     "start": "2026-10-13 19:30:00", "end": "2026-10-13 20:30:00",
                     "allday": False, "recurrence_id": dt, "recurrence_id_allday": False}
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v9", overrides=[exception],
                                exdates=[datetime(2026, 10, 19, 20, 0)]), self.config.id)
        self.assertEqual(lundi.start, datetime(2026, 10, 13, 19, 30))
        self.assertFalse(base.recurrence_id.calendar_event_ids.filtered(
            lambda e: e.x_nc_recurrence_id == datetime(2026, 10, 19, 20, 0)))

    def test_m1_separateurs_unicode_gardes(self):
        texte = SIMPLE.replace("SUMMARY:RÉSERVÉ", "DESCRIPTION:Rejoindre la rencontre Lien:https://x Code:1234\r\nSUMMARY:RÉSERVÉ")
        magasin = self._magasin(**{self.url_simple: texte})
        event = self._simple_nextcloud()
        event.write({"name": "Renommée"})
        corps = magasin.puts[-1][1]
        self.assertIn("DESCRIPTION:Rejoindre la rencontre Lien:https://x Code:1234", corps)
        self.assertEqual(corps.count("\r\nLien:"), 0)

    def test_m2_premiere_occurrence_renommee_ne_rase_plus(self):
        base, occurrences = self._serie()
        ids = sorted(occurrences.ids)
        base.with_context(skip_nc_sync=True).write(
            {"name": "Première renommée", "recurrence_update": "self_only"})
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v7"), self.config.id)
        self.assertEqual(sorted(base.recurrence_id.calendar_event_ids.ids), ids)

    def test_m2_premiere_supprimee_ne_revient_pas(self):
        self._magasin(**{self.url_serie: SERIE})
        base, occurrences = self._serie()
        suivantes = occurrences - base
        base.unlink()
        heritier = suivantes.filtered("x_caldav_href")
        self.assertEqual(heritier, suivantes.sorted("start")[:1])
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v8", exdates=[datetime(2026, 10, 5, 20, 0)]), self.config.id)
        self.assertTrue(all(suivantes.exists()))
        self.assertEqual(len(heritier.recurrence_id.calendar_event_ids), 5)

    def test_m3_serie_reecrite_dans_odoo_rendue_a_nextcloud(self):
        base, occurrences = self._serie()
        occurrences[2].write({"name": "Toute la série", "recurrence_update": "all_events"})
        porteur = self.env["calendar.event"].search([("x_nc_uid", "=", "serie-essai")])
        self.assertEqual(porteur.x_nc_serie_sig, porteur._BF_NC_SERIE_A_RECREER)
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v10"), self.config.id)
        porteur = self.env["calendar.event"].search([("x_nc_uid", "=", "serie-essai")])
        self.assertTrue(all(e.name == "Rencontre statutaire"
                            for e in porteur.recurrence_id.calendar_event_ids))

    def test_m4_lecture_lineaire(self):
        import time
        exceptions = "".join(
            "BEGIN:VEVENT\r\nUID:serie-essai\r\n"
            "RECURRENCE-ID;TZID=America/Toronto:%sT160000\r\n"
            "DTSTART;TZID=America/Toronto:%sT170000\r\n"
            "DTEND;TZID=America/Toronto:%sT180000\r\nSUMMARY:x\r\nEND:VEVENT\r\n"
            % (((datetime(2026, 1, 5) + timedelta(weeks=i)).strftime("%Y%m%d"),) * 3)
            for i in range(300))
        texte = SERIE.replace("END:VCALENDAR", exceptions + "END:VCALENDAR")
        debut = time.monotonic()
        data = self.config._parse_ics_vevent(texte)
        self.assertEqual(len(data["overrides"]), 300)
        self.assertLess(time.monotonic() - debut, 5)

    def test_m5_ressource_inattendue_dite(self):
        self._magasin(**{self.url_simple: SERIE.replace("serie-essai", "simple-essai")})
        event = self._simple_nextcloud()
        with patch.object(type(self.env.user), "_bus_send") as avis:
            event.write({"name": "Renommée"})
        self.assertEqual(self.config.last_sync_status, "error")
        self.assertTrue(avis.called)

    def test_range_thisandfuture_ecarte(self):
        exception = """BEGIN:VEVENT
UID:serie-essai
RECURRENCE-ID;RANGE=THISANDFUTURE;TZID=America/Toronto:20261012T160000
DTSTART;TZID=America/Toronto:20261012T170000
DTEND;TZID=America/Toronto:20261012T180000
SUMMARY:Rencontre statutaire
END:VEVENT
"""
        data = self.config._parse_ics_vevent(SERIE.replace("END:VCALENDAR", exception + "END:VCALENDAR"))
        self.assertEqual(data["overrides"], [])

    def test_parametres_du_titre_gardes(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE.replace(
            "SUMMARY:RÉSERVÉ", "SUMMARY;LANGUAGE=fr-CA:RÉSERVÉ")})
        event = self._simple_nextcloud()
        event.write({"name": "Renommée"})
        self.assertIn("SUMMARY;LANGUAGE=fr-CA:Renommée", magasin.puts[-1][1])


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestRetoucheSecondTour(BaseRetouche):
    """Séries au premier jour exclu, et autres cas limites."""

    def _ids_de_la_serie(self):
        porteur = self.env["calendar.event"].search([("x_nc_uid", "=", "serie-essai")])
        return porteur, porteur.recurrence_id.calendar_event_ids.sorted("start")

    def test_n1_serie_neuve_au_premier_jour_exclu(self):
        premier = datetime(2026, 10, 5, 20, 0)
        res = self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(exdates=[premier]), self.config.id)
        porteur, occurrences = self._ids_de_la_serie()
        self.assertEqual(res["action"], "created")
        self.assertEqual(res["id"], porteur.id)
        self.assertEqual(len(occurrences), 5)
        self.assertEqual(occurrences[0].start, datetime(2026, 10, 12, 20, 0))
        # La même série renommée dans Nextcloud est bien recréée, plus figée.
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(summary="Statutaire renommée", etag="v2", exdates=[premier]),
            self.config.id)
        porteur, occurrences = self._ids_de_la_serie()
        self.assertEqual(len(occurrences), 5)
        self.assertEqual(set(occurrences.mapped("name")), {"Statutaire renommée"})

    def test_n1_premier_jour_exclu_sur_serie_inchangee(self):
        base, occurrences = self._serie()
        res = self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v3", exdates=[datetime(2026, 10, 5, 20, 0)]),
            self.config.id)
        # Retirée de la série : effacée, ou mise à la corbeille quand
        # bf_calendar_trash est installé (crochet `_bf_nc_retirer_exclues`, 2.27.0).
        if "bf_trashed_at" in base._fields and base.exists():
            self.assertFalse(base.active)
            self.assertTrue(base.bf_trashed_at)
        else:
            self.assertFalse(base.exists())
        porteur, reste = self._ids_de_la_serie()
        self.assertEqual(res, {"id": porteur.id, "action": "updated"})
        self.assertEqual(reste, (occurrences - base).sorted("start"))

    def test_n1_toutes_les_dates_exclues_garde_le_porteur(self):
        base, occurrences = self._serie()
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v4", exdates=occurrences.mapped("start")), self.config.id)
        porteur, reste = self._ids_de_la_serie()
        self.assertEqual(len(porteur), 1)
        self.assertEqual(reste, porteur)

    def test_n2_serie_deplacee_dans_odoo_recreee_depuis_nextcloud(self):
        magasin = self._magasin(**{self.url_serie: SERIE})
        _base, occurrences = self._serie()
        troisieme = occurrences[2]
        with patch.object(type(self.env.user), "_bus_send") as avis:
            troisieme.write({"start": troisieme.start - timedelta(hours=1),
                             "stop": troisieme.stop - timedelta(hours=1),
                             "recurrence_update": "all_events"})
        self.assertFalse(magasin.puts)
        # L'usager sait que rien n'est parti.
        self.assertTrue(avis.called)
        porteur, _occ = self._ids_de_la_serie()
        self.assertEqual(porteur.x_nc_serie_sig, porteur._BF_NC_SERIE_A_RECREER)
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v11"), self.config.id)
        porteur, occurrences = self._ids_de_la_serie()
        self.assertEqual(occurrences[0].start, datetime(2026, 10, 5, 20, 0))
        self.assertEqual(occurrences[1].start, datetime(2026, 10, 12, 20, 0))
        self.assertNotEqual(porteur.x_nc_serie_sig, porteur._BF_NC_SERIE_A_RECREER)

    def test_n3_le_statut_ne_rend_pas_occupe(self):
        if "bf_event_status" not in self.env["calendar.event"]._fields:
            self.skipTest("bf_calendar_invite absent")
        texte = SIMPLE.replace("STATUS:TENTATIVE", "STATUS:CONFIRMED\nTRANSP:TRANSPARENT")
        magasin = self._magasin(**{self.url_simple: texte})
        event = self._simple_nextcloud()
        dispo = event.show_as
        event.write({"bf_event_status": "tentative"})
        corps = magasin.puts[-1][1]
        if event.show_as == dispo:
            self.assertIn("TRANSP:TRANSPARENT", corps)
            self.assertNotIn("TRANSP:OPAQUE", corps)
        self.assertIn("STATUS:TENTATIVE", corps)
        # Une disponibilité changée, elle, repart.
        event.write({"show_as": "free" if event.show_as == "busy" else "busy"})
        attendu = "TRANSP:TRANSPARENT" if event.show_as == "free" else "TRANSP:OPAQUE"
        self.assertIn(attendu, magasin.puts[-1][1])

    def test_securite_adresse_hors_de_la_collection(self):
        event = self._simple_nextcloud()
        refusees = [
            "http://127.0.0.1:18999/vol.ics",
            "https://nc.example.test.attaquant.test/remote.php/dav/calendars/alice/perso/x.ics",
            "http://nc.example.test/remote.php/dav/calendars/alice/perso/x.ics",
            "/remote.php/dav/calendars/alice/perso/../../bob/perso/x.ics",
            "/remote.php/dav/calendars/alice/perso/%2e%2e/x.ics",
            "/remote.php/dav/calendars/alice/autre/x.ics",
            "/remote.php/dav/calendars/alice/perso/",
            "/remote.php/dav/calendars/alice/perso/x.ics?y=1",
        ]
        for href in refusees:
            event.with_context(skip_nc_sync=True).write({"x_caldav_href": href})
            self.assertEqual(self.backend._resource_url(self.config, event), (None, None), href)
        acceptees = [
            "/remote.php/dav/calendars/alice/perso/x.ics",
            "https://nc.example.test/remote.php/dav/calendars/alice/perso/x.ics",
            "https://NC.example.test/remote.php/dav/calendars/alice/perso/x%20y.ics",
            # Nextcloud rend la casse de la requête.
            "/remote.php/dav/calendars/ALICE/Perso/x.ics",
        ]
        for href in acceptees:
            event.with_context(skip_nc_sync=True).write({"x_caldav_href": href})
            self.assertTrue(self.backend._resource_url(self.config, event)[0], href)
        # De bout en bout : la retouche ne part pas, et l'usager le sait.
        event.with_context(skip_nc_sync=True).write({"x_caldav_href": refusees[0]})
        with patch(REQUESTS + ".get") as get, patch(REQUESTS + ".put") as put, \
                patch.object(type(self.env.user), "_bus_send") as avis:
            event.write({"name": "Renommée"})
        self.assertFalse(get.called or put.called)
        self.assertTrue(avis.called)
        # Ni par le webhook (n8n écrit avec les mêmes identifiants).
        event.with_context(skip_nc_sync=True).write({"x_sync_source": "odoo"})
        charge = event._get_sync_payload("update")
        self.assertIsNone(charge["event"]["caldav_resource_url"])

    def test_lecteur_value_date_time_et_tzid_entre_guillemets(self):
        dt, journee = self.config._parse_ics_datetime(
            "20261012T160000", ';VALUE=DATE-TIME;TZID="America/Toronto"')
        self.assertEqual((dt, journee), (datetime(2026, 10, 12, 20, 0), False))
        texte = SIMPLE.replace(
            "DTSTART;TZID=America/Toronto:", 'DTSTART;VALUE=DATE-TIME;TZID="America/Toronto":')
        data = self.config._parse_ics_vevent(texte)
        self.assertEqual(fields.Datetime.to_datetime(data["start"]), datetime(2026, 10, 13, 20, 0))
        self.assertFalse(data["allday"])

    def test_deux_points_dans_un_parametre_entre_guillemets(self):
        texte = SIMPLE.replace("SUMMARY:RÉSERVÉ", 'SUMMARY;ALTREP="http://x.test/a":RÉSERVÉ')
        self.assertEqual(self.config._parse_ics_vevent(texte)["summary"],
                         "RÉSERVÉ si Alex souhaite déplacer statutaire")
        magasin = self._magasin(**{self.url_simple: texte})
        event = self._simple_nextcloud()
        event.write({"name": "Renommée"})
        self.assertIn('SUMMARY;ALTREP="http://x.test/a":Renommée', magasin.puts[-1][1])

    def test_guillemet_impair_ne_rend_pas_la_ligne_illisible(self):
        texte = SIMPLE.replace(
            "SUMMARY:RÉSERVÉ",
            'ATTENDEE;CN="Rene O\\"Hara":mailto:rene.essai@example.invalid\n'
            'DESCRIPTION;X-NOTE=a"b:Texte de la note\n'
            'SUMMARY;X-NOTE=a"b:RÉSERVÉ')
        data = self.config._parse_ics_vevent(texte)
        self.assertIn("rene.essai@example.invalid", data["attendees"])
        self.assertEqual(data["description"], "Texte de la note")
        self.assertEqual(data["summary"], "RÉSERVÉ si Alex souhaite déplacer statutaire")
        magasin = self._magasin(**{self.url_simple: texte})
        event = self._simple_nextcloud()
        event.write({"name": "Renommée"})
        self.assertIn('SUMMARY;X-NOTE=a"b:Renommée\r\n', magasin.puts[-1][1])

    def test_suppression_d_une_adresse_refusee_le_dit(self):
        event = self._simple_nextcloud()
        event.with_context(skip_nc_sync=True).write({"x_caldav_href": "http://127.0.0.1:18999/vol.ics"})
        with patch(REQUESTS + ".delete") as delete:
            with self.assertRaisesRegex(ValueError, "URL"):
                event._push_caldav("delete", event, self.config)
        self.assertFalse(delete.called)
