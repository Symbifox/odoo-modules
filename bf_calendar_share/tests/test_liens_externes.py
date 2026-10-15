# -*- coding: utf-8 -*-
"""Liens de partage vers l'extérieur (lot 5), servis en HTTP."""

from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_calendar_share")
class TestLiensExternes(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_mail_to_attendees=True, skip_nc_sync=True))
        cls.proprio = new_test_user(cls.env, login="proprio_lien", groups="base.group_user",
                                    tz="America/Toronto")
        cls.proprio.with_user(cls.proprio).calendar_default_privacy = "confidential"
        cls.collegue = new_test_user(cls.env, login="collegue_lien", groups="base.group_user")
        cls.client = cls.env["res.partner"].create({"name": "Client Tiers", "email": "tiers@example.invalid"})
        demain = fields.Datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
        E = cls.env["calendar.event"].with_user(cls.proprio)
        cls.interne = E.create({
            "name": "Audit chez Client X", "location": "Bureau du client",
            "description": "<p>Ordre du jour détaillé</p>", "start": demain, "stop": demain + timedelta(hours=1),
            "videocall_location": "https://visio.example.invalid/secret-room",
            "partner_ids": [(6, 0, (cls.proprio.partner_id | cls.client).ids)]})
        cls.prive = E.create({
            "name": "Rendez-vous médical", "location": "Clinique", "privacy": "private",
            "start": demain + timedelta(hours=3), "stop": demain + timedelta(hours=4)})
        cls.libre = E.create({
            "name": "Plage ouverte", "show_as": "free",
            "start": demain + timedelta(hours=5), "stop": demain + timedelta(hours=6)})
        cls.annule_nom = "Rencontre annulée"
        cls.Link = cls.env["bf.calendar.share.link"].with_user(cls.proprio)

    def _lien(self, niveau, **vals):
        return self.Link.create(dict({"name": "Cabinet", "level": niveau}, **vals))

    def _get(self, url):
        reponse = self.url_open(url.replace(self.base_url(), ""))
        return reponse

    # -- niveaux --------------------------------------------------------------

    def test_disponibilites_seulement(self):
        lien = self._lien("freebusy")
        r = self._get(lien.url_ics)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/calendar", r.headers["Content-Type"])
        corps = r.text
        for secret in ("Audit", "Client X", "Bureau du client", "Rendez-vous médical", "Clinique",
                       "Ordre du jour", "tiers@example.invalid", "secret-room", "Plage ouverte"):
            self.assertNotIn(secret, corps)
        self.assertEqual(corps.count("BEGIN:VEVENT"), 2)  # l'événement libre n'y est pas

    def test_titres_et_lieux_montre_les_internes_seulement(self):
        corps = self._get(self._lien("titles").url_ics).text
        self.assertIn("SUMMARY:Audit chez Client X", corps)
        self.assertIn("LOCATION:Bureau du client", corps)
        self.assertNotIn("Ordre du jour", corps)
        self.assertIn("TRANSP:TRANSPARENT", corps)  # l'événement libre, marqué libre
        self.assertNotIn("Rendez-vous médical", corps)
        self.assertNotIn("Clinique", corps)

    def test_tous_les_details_sans_participants_ni_visio(self):
        corps = self._get(self._lien("details").url_ics).text
        self.assertIn("DESCRIPTION:Ordre du jour détaillé", corps)
        for secret in ("tiers@example.invalid", "Client Tiers", "secret-room", "ATTENDEE",
                       "Rendez-vous médical"):
            self.assertNotIn(secret, corps)

    def test_defaut_prive_de_l_organisateur_reste_occupe(self):
        self.proprio.with_user(self.proprio).calendar_default_privacy = "private"
        corps = self._get(self._lien("details").url_ics).text
        self.assertNotIn("Audit", corps)

    # -- validité -------------------------------------------------------------

    def test_jeton_inconnu_revoque_expire_404(self):
        lien = self._lien("titles")
        self.assertEqual(self.url_open("/calendar/share/%s.ics" % ("x" * 43)).status_code, 404)
        expire = self._lien("titles", date_expiry=fields.Date.today() - timedelta(days=2))
        self.assertEqual(self._get(expire.url_ics).status_code, 404)
        ancienne = lien.url_ics
        lien.action_revoke()
        self.assertEqual(self._get(ancienne).status_code, 404)

    def test_nouvelle_adresse_tue_l_ancienne(self):
        lien = self._lien("titles")
        ancienne = lien.url_ics
        lien.action_new_address()
        self.assertEqual(self._get(ancienne).status_code, 404)
        self.assertEqual(self._get(lien.url_ics).status_code, 200)

    def test_expiration_par_defaut_90_jours(self):
        lien = self._lien("titles")
        # Le jour de la personne qui partage (son fuseau), pas celui d'UTC.
        self.assertEqual(lien.date_expiry, fields.Date.context_today(lien) + timedelta(days=90))

    # -- autres formes --------------------------------------------------------

    def test_disponibilites_vfreebusy(self):
        corps = self._get(self._lien("details").url_freebusy).text
        self.assertIn("BEGIN:VFREEBUSY", corps)
        self.assertEqual(corps.count("FREEBUSY;FBTYPE=BUSY:"), 2)
        self.assertNotIn("Audit", corps)

    def test_page_web(self):
        lien = self._lien("titles")
        r = self._get(lien.url_page)
        self.assertEqual(r.status_code, 200)
        # Le doctype en tête, jamais échappé en texte visible (captures du 2026-10-15).
        self.assertTrue(r.text.startswith("<!DOCTYPE html>"), r.text[:60])
        self.assertNotIn("&lt;!DOCTYPE", r.text)
        self.assertIn("Audit chez Client X", r.text)
        self.assertNotIn("Rendez-vous médical", r.text)
        self.assertEqual(r.headers.get("X-Robots-Tag"), "noindex, nofollow")
        self.assertNotIn("session_id", r.headers.get("Set-Cookie", ""))

    def test_acces_journalises(self):
        lien = self._lien("freebusy")
        self._get(lien.url_ics)
        self._get(lien.url_page)
        self.assertEqual(sorted(lien.access_ids.mapped("kind")), ["ics", "page"])

    # -- droits ---------------------------------------------------------------

    def test_lien_invisible_aux_collegues(self):
        lien = self._lien("details")
        with self.assertRaises(AccessError):
            lien.with_user(self.collegue).read(["token"])
        self.assertFalse(self.env["bf.calendar.share.link"].with_user(self.collegue).search(
            [("id", "=", lien.id)]))

    def test_rappel_avant_expiration_et_menage(self):
        lien = self._lien("titles", date_expiry=fields.Date.today() + timedelta(days=3))
        self.env["bf.calendar.share.access"].sudo().create({
            "link_id": lien.id, "kind": "ics", "date": datetime.now() - timedelta(days=400)})
        self.env["bf.calendar.share.link"]._cron_bf_rappels_et_menage()
        self.assertEqual(len(lien.activity_ids), 1)
        self.assertFalse(lien.access_ids)
        self.env["bf.calendar.share.link"]._cron_bf_rappels_et_menage()
        self.assertEqual(len(lien.activity_ids), 1)
