"""The calendar's trash.

The case it was born of: a meeting kept in
Nextcloud, cancelled, then archived, then back in the agenda 13 seconds later,
active and busy, because the pull did not see archived meetings.

Nextcloud is the in-memory CalDAV store of the sync's own tests, taught
`If-None-Match: *` (a restore must not overwrite what took the place).
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.calendar_nextcloud_sync.models import caldav_backend as cb
from odoo.addons.calendar_nextcloud_sync.tests.test_retouche import (
    REQUESTS, SERIE, SIMPLE, BaseRetouche, Magasin,
)
from odoo.addons.bf_calendar_trash import _bf_trash_existing_archives

TRASH_REQUESTS = "odoo.addons.bf_calendar_trash.models.calendar_event.requests"


class MagasinCorbeille(Magasin):

    def put(self, url, data=None, headers=None, **kwargs):
        headers = headers or {}
        if headers.get("If-None-Match") == "*" and url in self.ressources:
            return SimpleNamespace(status_code=412, headers={}, raise_for_status=lambda: None)
        return super().put(url, data=data, headers=headers, **kwargs)


def _master_rrule(texte):
    lignes = cb.deplier(texte)
    for d, f in cb.blocs_vevent(lignes):
        tete = cb.props_de_tete(lignes[d:f + 1])
        if not any(cb.nom_prop(ligne) == "RECURRENCE-ID" for _i, ligne in tete):
            return next((cb.valeur_prop(ligne) for _i, ligne in tete
                         if cb.nom_prop(ligne) == "RRULE"), None)
    return None


@tagged("post_install", "-at_install", "bf_calendar_trash")
class TestTrash(BaseRetouche):

    def _magasin(self, **ressources):
        magasin = MagasinCorbeille(ressources)
        for cible in (REQUESTS, TRASH_REQUESTS):
            for verbe in ("get", "put", "delete"):
                p = patch(cible + "." + verbe, side_effect=getattr(magasin, verbe))
                p.start()
                self.addCleanup(p.stop)
        return magasin

    def _local(self, **vals):
        return self.env["calendar.event"].create(dict({
            "name": "Rencontre d'Odoo seulement",
            "start": datetime(2026, 10, 21, 14, 0),
            "stop": datetime(2026, 10, 21, 14, 45),
        }, **vals))

    def _serie_magasin(self):
        magasin = self._magasin(**{self.url_serie: SERIE})
        base, occurrences = self._serie()
        self.assertEqual(len(occurrences), 6)
        return magasin, base, occurrences

    # -- a meeting of its own ------------------------------------------------

    def test_archive_moves_to_trash_and_leaves_nextcloud(self):
        """The calendar's "Delete" (core: action_archive) is the trash, and Nextcloud follows."""
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        event.with_context(bf_trash_door=True).action_archive()
        self.assertFalse(event.active)
        self.assertTrue(event.bf_trashed_at)
        self.assertEqual(event.bf_trashed_by, self.env.user)
        self.assertEqual(event.bf_trash_origin, "odoo")
        self.assertEqual(event.bf_trash_nc_state, "deleted")
        self.assertNotIn(self.url_simple, magasin.ressources)
        self.assertEqual(event.sudo().bf_trash_nc_ics, SIMPLE)

    def test_restore_puts_the_resource_back(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        event.with_context(bf_trash_door=True).action_archive()
        event.action_unarchive()
        self.assertTrue(event.active)
        self.assertFalse(event.bf_trashed_at)
        self.assertFalse(event.bf_trash_batch)
        self.assertEqual(magasin.ressources.get(self.url_simple), SIMPLE)
        self.assertEqual(magasin.puts[-1][2].get("If-None-Match"), "*")
        self.assertFalse(event.sudo().bf_trash_nc_ics)

    def test_restore_never_overwrites_what_took_its_place(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        event.with_context(bf_trash_door=True).action_archive()
        magasin.ressources[self.url_simple] = SIMPLE.replace("STATUS:TENTATIVE", "STATUS:CONFIRMED")
        magasin.etags[self.url_simple] = '"autre"'
        event.action_unarchive()
        self.assertTrue(event.active)
        self.assertIn("STATUS:CONFIRMED", magasin.ressources[self.url_simple])

    def test_pull_does_not_bring_back_an_archived_meeting(self):
        """The original defect: a pull while Nextcloud still has it."""
        self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        event.with_context(bf_trash_bypass=True, skip_nc_sync=True).action_archive()
        avant = self.env["calendar.event"].with_context(active_test=False).search_count(
            [("x_nc_uid", "=", "simple-essai")])
        res = self.env["calendar.event"].create_from_nextcloud({
            "uid": "simple-essai", "summary": "RÉSERVÉ si Alex souhaite déplacer statutaire",
            "start": "2026-10-13 20:00:00", "end": "2026-10-13 21:00:00", "allday": False,
            "etag": "v9", "href": "/remote.php/dav/calendars/alice/perso/simple-essai.ics",
            "attendees": [], "alarms": None, "status": None,
        }, self.config.id)
        self.assertEqual(res.get("action"), "skipped")
        apres = self.env["calendar.event"].with_context(active_test=False).search(
            [("x_nc_uid", "=", "simple-essai")])
        self.assertEqual(len(apres), avant)
        self.assertFalse(apres.active)
        self.assertEqual(apres.x_caldav_etag, "v9")

    def test_meeting_without_nextcloud(self):
        with patch(REQUESTS + ".get") as get, patch(REQUESTS + ".delete") as delete:
            event = self._local()
            event.with_context(bf_trash_door=True).action_archive()
        get.assert_not_called()
        delete.assert_not_called()
        self.assertTrue(event.bf_trashed_at)
        self.assertFalse(event.bf_trash_nc_state)

    # -- the doors -------------------------------------------------------------

    def test_web_delete_trashes_then_purges(self):
        event = self._local()
        door = event.with_context(bf_trash_door=True)
        door.unlink()
        self.assertTrue(event.exists())
        self.assertFalse(event.active)
        self.assertTrue(event.bf_trashed_at)
        door.unlink()
        self.assertFalse(event.exists())

    def test_doors_are_read_off_the_request(self):
        event = self._local()
        cible = "odoo.addons.bf_calendar_trash.models.calendar_event.request"
        with patch(cible, SimpleNamespace(params={"model": "calendar.event", "method": "unlink"})):
            self.assertTrue(event._bf_trash_door("unlink"))
            self.assertFalse(event._bf_trash_door("action_mass_archive"))
            self.assertFalse(event.with_context(skip_nc_sync=True)._bf_trash_door("unlink"))
        with patch(cible, SimpleNamespace(params={"model": "resource.booking", "method": "unlink"})):
            self.assertFalse(event._bf_trash_door("unlink"))
        with patch(cible, None):
            self.assertFalse(event._bf_trash_door("unlink"))

    def test_code_archive_is_not_the_trash(self):
        """Core archives for its own housekeeping: neither trash nor Nextcloud."""
        with patch(REQUESTS + ".delete") as delete:
            event = self._simple_nextcloud()
            event.action_archive()
        delete.assert_not_called()
        self.assertFalse(event.active)
        self.assertFalse(event.bf_trashed_at)

    def test_series_rewrite_by_core_leaves_nextcloud_alone(self):
        """`_rewrite_recurrence` archives through action_mass_archive (test_b1 of the sync)."""
        magasin, base, occ = self._serie_magasin()
        occ[1].write({"start": datetime(2026, 10, 12, 21, 0), "stop": datetime(2026, 10, 12, 22, 0),
                      "recurrence_update": "all_events"})
        self.assertIn(self.url_serie, magasin.ressources)
        trash = self.env["calendar.event"].with_context(active_test=False).search(
            [("bf_trashed_at", "!=", False), ("id", "in", occ.ids)])
        self.assertFalse(trash)

    def test_code_unlink_still_deletes(self):
        """Raze and recreate, regeneration, cleanups: never the trash."""
        event = self._local()
        event.unlink()
        self.assertFalse(event.exists())

    def test_purge_refuses_a_live_meeting(self):
        event = self._local()
        with self.assertRaises(Exception):
            event.action_bf_purge()
        event.with_context(bf_trash_door=True).action_archive()
        event.action_bf_purge()
        self.assertFalse(event.exists())

    def test_deleted_in_nextcloud_lands_in_trash(self):
        with patch(REQUESTS + ".delete") as delete:
            event = self._simple_nextcloud()
            res = self.env["calendar.event"].delete_from_nextcloud("simple-essai", self.config.id)
        delete.assert_not_called()
        self.assertTrue(res.get("trashed"))
        self.assertTrue(event.exists())
        self.assertFalse(event.active)
        self.assertEqual(event.bf_trash_origin, "nextcloud")

    def test_cancel_dialog_moves_to_trash(self):
        event = self._local()
        wizard = self.env["bf.calendar.event.cancel"].create({
            "event_ids": [(6, 0, event.ids)], "bf_trash": True})
        wizard.action_apply()
        self.assertEqual(event.bf_event_status, "cancelled")
        self.assertFalse(event.active)
        self.assertTrue(event.bf_trashed_at)

    def test_cancel_dialog_without_trash_keeps_the_meeting(self):
        event = self._local()
        self.env["bf.calendar.event.cancel"].create({
            "event_ids": [(6, 0, event.ids)]}).action_apply()
        self.assertTrue(event.active)
        self.assertFalse(event.bf_trashed_at)

    def test_existing_archives_enter_the_trash(self):
        event = self._local()
        event.with_context(bf_trash_bypass=True).action_archive()
        self.assertFalse(event.bf_trashed_at)
        event.flush_recordset()
        _bf_trash_existing_archives(self.env)
        event.invalidate_recordset()
        self.assertTrue(event.bf_trashed_at)
        self.assertEqual(event.bf_trash_batch, "before-%s" % event.id)

    # -- series ----------------------------------------------------------------

    def test_one_date_of_a_series_is_an_exdate(self):
        magasin, base, occ = self._serie_magasin()
        occ[2].with_context(bf_trash_door=True).action_mass_archive("self_only")
        self.assertFalse(occ[2].active)
        self.assertEqual(occ[2].bf_trash_nc_state, "edited")
        self.assertIn("EXDATE", magasin.ressources[self.url_serie])
        self.assertTrue(base.active)
        occ[2].action_unarchive()
        self.assertTrue(occ[2].active)
        self.assertEqual(cb.deplier(magasin.ressources[self.url_serie]), cb.deplier(SERIE))

    def test_the_carrier_hands_the_series_over(self):
        magasin, base, occ = self._serie_magasin()
        porteur = occ[0]._bf_nc_porteur()
        porteur.with_context(bf_trash_door=True).action_mass_archive("self_only")
        heritier = (occ - porteur).sorted("start")[:1]
        self.assertTrue(heritier.x_caldav_href)
        self.assertEqual(heritier.x_nc_uid, "serie-essai")
        self.assertIn("EXDATE", magasin.ressources[self.url_serie])

    def test_following_dates_end_the_series(self):
        magasin, base, occ = self._serie_magasin()
        occ[3].with_context(bf_trash_door=True).action_mass_archive("future_events")
        self.assertTrue(all(not e.active for e in occ[3:]))
        self.assertTrue(all(e.active for e in occ[:3]))
        regle = _master_rrule(magasin.ressources[self.url_serie])
        self.assertIn("UNTIL=20261026T195959Z", regle)
        self.assertNotIn("COUNT", regle)
        # The pull reads the series again and finds it unchanged: no raze.
        porteur = occ[0]._bf_nc_porteur()
        data = self.config._parse_ics_vevent(magasin.ressources[self.url_serie])
        self.assertEqual(porteur.x_nc_serie_sig, porteur._bf_nc_signature(data))
        # Restored: the dates are back in their series, and Nextcloud whole.
        occ[3].action_unarchive()
        self.assertTrue(all(e.active for e in occ))
        self.assertEqual(set(occ[3:].mapped("recurrence_id")), {base.recurrence_id})
        self.assertEqual(cb.deplier(magasin.ressources[self.url_serie]), cb.deplier(SERIE))

    def test_whole_series_deletes_the_resource(self):
        magasin, base, occ = self._serie_magasin()
        occ[0].with_context(bf_trash_door=True).action_mass_archive("all_events")
        self.assertTrue(all(not e.active for e in occ))
        self.assertNotIn(self.url_serie, magasin.ressources)
        occ[4].action_unarchive()
        self.assertTrue(all(e.active for e in occ))
        self.assertEqual(cb.deplier(magasin.ressources.get(self.url_serie)), cb.deplier(SERIE))

    def test_from_the_first_date_is_the_whole_series(self):
        magasin, base, occ = self._serie_magasin()
        occ[0].with_context(bf_trash_door=True).action_mass_archive("future_events")
        self.assertTrue(base.recurrence_id.exists())
        self.assertNotIn(self.url_serie, magasin.ressources)

    def test_date_deleted_in_nextcloud_lands_in_trash_and_comes_back(self):
        magasin, base, occ = self._serie_magasin()
        lundi = datetime(2026, 10, 19, 20, 0)
        magasin.ressources[self.url_serie] = SERIE.replace(
            "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=6",
            "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=6\r\nEXDATE;TZID=America/Toronto:20261019T160000")
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(etag="v5", exdates=[lundi]), self.config.id)
        self.assertTrue(occ[2].exists())
        self.assertFalse(occ[2].active)
        self.assertEqual(occ[2].bf_trash_origin, "nextcloud")
        occ[2].action_unarchive()
        self.assertTrue(occ[2].active)
        self.assertNotIn("EXDATE", magasin.ressources[self.url_serie])

    def test_recreated_series_still_deletes_its_excluded_dates(self):
        """A series changed in Nextcloud is razed and recreated: not a deletion."""
        self._magasin(**{self.url_serie: SERIE})
        _base, occ = self._serie()
        self.env["calendar.event"].create_from_nextcloud(
            self._donnees_serie(summary="Statutaire renommée", etag="v6",
                                exdates=[datetime(2026, 10, 19, 20, 0)]), self.config.id)
        trash = self.env["calendar.event"].with_context(active_test=False).search([
            ("bf_trashed_at", "!=", False), ("x_nc_calendar_id", "=", self.config.id)])
        self.assertFalse(trash)

    def test_restore_after_a_change_in_nextcloud_leaves_it(self):
        """The rule was changed in Nextcloud since the series was cut: left as it is."""
        magasin, base, occ = self._serie_magasin()
        occ[3].with_context(bf_trash_door=True).action_mass_archive("future_events")
        change = magasin.ressources[self.url_serie].replace("FREQ=WEEKLY", "FREQ=WEEKLY;INTERVAL=2")
        magasin.ressources[self.url_serie] = change
        magasin.etags[self.url_serie] = '"change-ailleurs"'
        occ[3].action_unarchive()
        self.assertTrue(occ[3].active)
        self.assertEqual(magasin.ressources[self.url_serie], change)

    # -- portes, purge, restauration : cas limites ---------------------------

    def test_b1_popover_delete_dialog_on_a_series(self):
        """"Delete" on a series without Google/Microsoft opens core's dialog."""
        magasin, base, occ = self._serie_magasin()
        Wizard = self.env["calendar.popover.delete.wizard"]
        Wizard.create({"record": occ[3].id, "delete": "next"}).close()
        self.assertTrue(all(e.exists() and not e.active and e.bf_trashed_at for e in occ[3:]))
        self.assertIn("UNTIL=", _master_rrule(magasin.ressources[self.url_serie]))
        Wizard.create({"record": occ[0].id, "delete": "all"}).close()
        self.assertTrue(all(e.exists() and not e.active for e in occ[:3]))
        self.assertNotIn(self.url_serie, magasin.ressources)
        seule = self._local()
        Wizard.create({"record": seule.id, "delete": "one"}).close()
        self.assertTrue(seule.exists() and seule.bf_trashed_at)

    def test_m1_restore_in_any_order(self):
        magasin, base, occ = self._serie_magasin()
        door = lambda e: e.with_context(bf_trash_door=True)  # noqa: E731
        door(occ[1]).action_mass_archive("self_only")
        door(occ[3]).action_mass_archive("self_only")
        occ[1].action_unarchive()
        texte = magasin.ressources[self.url_serie]
        self.assertTrue(occ[1].active)
        self.assertNotIn("20261012T160000", texte)
        self.assertIn("20261026T160000", texte)
        occ[3].action_unarchive()
        self.assertNotIn("EXDATE", magasin.ressources[self.url_serie])

    def test_m1_series_end_restored_around_a_later_exdate(self):
        magasin, base, occ = self._serie_magasin()
        door = lambda e: e.with_context(bf_trash_door=True)  # noqa: E731
        door(occ[4]).action_mass_archive("future_events")
        door(occ[1]).action_mass_archive("self_only")
        occ[4].action_unarchive()
        texte = magasin.ressources[self.url_serie]
        self.assertEqual(_master_rrule(texte), "FREQ=WEEKLY;BYDAY=MO;COUNT=6")
        self.assertIn("EXDATE", texte)
        self.assertFalse(occ[1].active)

    def test_m2_purge_removes_what_nextcloud_still_has(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        event.with_context(bf_trash_bypass=True, skip_nc_sync=True).action_archive()
        event.flush_recordset()
        _bf_trash_existing_archives(self.env)
        event.invalidate_recordset()
        with patch(REQUESTS + ".delete", side_effect=RuntimeError("panne")):
            with self.assertRaises(UserError):
                event.action_bf_purge()
        self.assertTrue(event.exists())
        event.action_bf_purge()
        self.assertFalse(event.exists())
        self.assertNotIn(self.url_simple, magasin.ressources)

    def test_m3_restored_in_nextcloud_comes_back(self):
        self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        self.env["calendar.event"].delete_from_nextcloud("simple-essai", self.config.id)
        self.assertFalse(event.active)
        res = self.env["calendar.event"].create_from_nextcloud({
            "uid": "simple-essai", "summary": "RÉSERVÉ si Alex souhaite déplacer statutaire",
            "start": "2026-10-13 20:00:00", "end": "2026-10-13 21:00:00", "allday": False,
            "etag": "v4", "href": "/remote.php/dav/calendars/alice/perso/simple-essai.ics",
            "attendees": [], "alarms": None, "status": None,
        }, self.config.id)
        self.assertEqual(res.get("id"), event.id)
        self.assertTrue(event.active)
        self.assertFalse(event.bf_trashed_at)
        self.assertEqual(self.env["calendar.event"].search_count(
            [("x_nc_uid", "=", "simple-essai")]), 1)

    def test_m4_appointment_meeting_is_refused(self):
        if "resource.booking" not in self.env:
            self.skipTest("resource_booking absent")
        booking = self.env["resource.booking"].sudo().search([
            ("meeting_id", "!=", False), ("meeting_id.active", "=", True),
            ("state", "!=", "canceled")], limit=1)
        if not booking:
            self.skipTest("aucun rendez-vous lié à une rencontre dans cette base")
        with self.assertRaises(UserError):
            booking.meeting_id.with_context(bf_trash_door=True).unlink()
        self.assertTrue(booking.meeting_id.active)

    def test_m6_one_batch_per_meeting(self):
        a, b = self._local(), self._local(name="Autre rencontre")
        (a | b).with_context(bf_trash_door=True).unlink()
        self.assertNotEqual(a.bf_trash_batch, b.bf_trash_batch)
        a.action_unarchive()
        self.assertTrue(a.active)
        self.assertFalse(b.active)

    def test_m8_reactivated_by_code_leaves_the_trash(self):
        event = self._local()
        event.with_context(bf_trash_door=True).action_archive()
        event.write({"active": True})
        self.assertFalse(event.bf_trashed_at)
        self.assertFalse(event.bf_trash_batch)

    def test_m2_activity_deleted_with_its_meeting(self):
        event = self._local()
        activity = self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": self.env.user.partner_id.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_meeting").id,
            "calendar_event_id": event.id,
            "summary": "Rencontre",
        })
        activity.unlink_w_meeting()
        self.assertTrue(event.exists())
        self.assertTrue(event.bf_trashed_at)

    # -- doublons, dates déplacées, activités -------------------------------

    def _doublon_archive(self, vivante):
        """Archived before the trash, same address as a live meeting."""
        doublon = vivante.with_context(skip_nc_sync=True).copy({
            "x_nc_uid": vivante.x_nc_uid, "x_caldav_href": vivante.x_caldav_href,
            "x_nc_calendar_id": vivante.x_nc_calendar_id.id})
        doublon.with_context(bf_trash_bypass=True, skip_nc_sync=True).action_archive()
        doublon.flush_recordset()
        _bf_trash_existing_archives(self.env)
        doublon.invalidate_recordset()
        return doublon

    def test_r1_purging_a_duplicate_leaves_the_live_resource(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        vivante = self._simple_nextcloud()
        doublon = self._doublon_archive(vivante)
        self.assertTrue(doublon.bf_trashed_at)
        doublon.action_bf_purge()
        self.assertFalse(doublon.exists())
        self.assertIn(self.url_simple, magasin.ressources)
        self.assertTrue(vivante.active)

    def test_r2_restore_beside_an_archived_duplicate_keeps_the_link(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        vivante = self._simple_nextcloud()
        self._doublon_archive(vivante)
        vivante.with_context(bf_trash_door=True).action_archive()
        self.assertNotIn(self.url_simple, magasin.ressources)
        vivante.with_context(active_test=False).action_unarchive()
        self.assertTrue(vivante.active)
        self.assertEqual(vivante.x_nc_uid, "simple-essai")
        self.assertIn(self.url_simple, magasin.ressources)

    def test_ma_moved_date_gets_its_exception_back(self):
        texte = SERIE.replace("END:VEVENT\nEND:VCALENDAR",
            "END:VEVENT\nBEGIN:VEVENT\nUID:serie-essai\n"
            "RECURRENCE-ID;TZID=America/Toronto:20261019T160000\n"
            "DTSTART;TZID=America/Toronto:20261019T170000\nDTEND;TZID=America/Toronto:20261019T180000\n"
            "SUMMARY:Rencontre statutaire\nEND:VEVENT\nEND:VCALENDAR")
        magasin = self._magasin(**{self.url_serie: texte})
        base, occ = self._serie()
        lundi = occ.filtered(lambda e: e.start.date().isoformat() == "2026-10-19")
        lundi.with_context(bf_trash_door=True).action_mass_archive("self_only")
        self.assertNotIn("RECURRENCE-ID", magasin.ressources[self.url_serie])
        lundi.action_unarchive()
        apres = magasin.ressources[self.url_serie]
        self.assertNotIn("EXDATE", apres)
        self.assertIn("RECURRENCE-ID;TZID=America/Toronto:20261019T160000", apres)

    def test_mb_activity_leaves_a_trashed_meeting_in_the_trash(self):
        event = self._local()
        activity = self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": self.env.user.partner_id.id,
            "activity_type_id": self.env.ref("mail.mail_activity_data_meeting").id,
            "calendar_event_id": event.id,
            "summary": "Rencontre",
        })
        event.with_context(bf_trash_door=True).action_archive()
        activity.unlink_w_meeting()
        self.assertTrue(event.exists())
        self.assertTrue(event.bf_trashed_at)

    def test_m9_google_meeting_leaves_google_and_comes_back(self):
        google = self.env["nextcloud.calendar.sync.config"].create({
            "name": "Google essai", "backend_type": "google", "sync_direction": "both",
            "google_calendar_id": "primary", "calendar_owner_id": self.env.ref("base.user_admin").id})
        event = self._local(x_nc_calendar_id=google.id, x_nc_uid="gid-essai",
                            x_sync_source="google")
        Backend = type(self.env["calendar.google.backend"])
        with patch.object(Backend, "push_delete") as delete, \
                patch.object(Backend, "push_create", return_value=("gid-neuf", "e1")) as create:
            event.with_context(bf_trash_door=True).action_archive()
            delete.assert_called_once()
            event.action_unarchive()
            create.assert_called_once()
        self.assertTrue(event.active)
        self.assertEqual(event.x_nc_uid, "gid-neuf")

    def test_nextcloud_failure_is_kept_on_the_meeting(self):
        magasin = self._magasin(**{self.url_simple: SIMPLE})
        event = self._simple_nextcloud()
        with patch(REQUESTS + ".delete", side_effect=RuntimeError("panne")):
            event.with_context(bf_trash_door=True).action_archive()
        self.assertFalse(event.active)
        self.assertEqual(event.bf_trash_nc_state, "failed")
        self.assertIn(self.url_simple, magasin.ressources)
