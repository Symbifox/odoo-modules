# -*- coding: utf-8 -*-
"""Le portail écrit les heures et l'heure comme le client les lit.

Vu à la démo : « Dernière mise à jour : 2026-10-10 22:43 » pour 18 h 43 à
Montréal, et « 16.20 h » sur une page française.
"""
from datetime import date, datetime, timedelta

import pytz

from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPortalAffichage(HttpCase):

    def test_heure_locale_et_virgule_decimale(self):
        if not self.env["res.lang"]._lang_get("fr_CA"):
            self.skipTest("fr_CA inactif sur cette base")
        client = self.env["res.partner"].create({"name": "Banc HB : client", "is_company": True})
        portail = new_test_user(self.env, login="banc_hb_portail", groups="base.group_portal",
                                password="banc_hb_portail", lang="fr_CA",
                                tz="America/Toronto")
        portail.partner_id.parent_id = client
        projet = self.env["project.project"].create({
            "name": "Banc HB : projet", "partner_id": client.id, "allow_timesheets": True})
        employe = self.env["hr.employee"].create({"name": "Banc HB : employé"})
        self.env["account.analytic.line"].create({
            "name": "travail", "date": date.today(), "unit_amount": 16.2,
            "project_id": projet.id, "employee_id": employe.id})
        banque = self.env["hour.bank.client"].create({
            "partner_id": client.id, "project_ids": [(6, 0, projet.ids)],
            "threshold_mode": "disabled", "notify_internal_followers": False,
        })
        self.authenticate("banc_hb_portail", "banc_hb_portail")
        avant = datetime.utcnow().replace(second=0, microsecond=0)
        corps = self.url_open("/my/hour-banks/%s" % banque.id).text
        self.assertIn("16,20", corps)
        self.assertNotIn("16.20", corps)
        local = pytz.utc.localize(avant).astimezone(pytz.timezone("America/Toronto"))
        lus = {(local + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M") for m in (0, 1)}
        self.assertTrue(any(l in corps for l in lus), "heure de Montréal attendue")
        self.assertNotIn(avant.strftime("%Y-%m-%d %H:%M"), corps)
        # Le PDF envoyé au client porte les mêmes nombres, à l'heure du CLIENT :
        # envoyé d'Auckland (ou par l'usager système, sans fuseau), il ne doit
        # pas dater le document d'une autre heure que la sienne.
        client.tz = "America/Toronto"
        self.env.user.write({"lang": "fr_CA", "tz": "Pacific/Auckland"})
        avant = datetime.utcnow().replace(second=0, microsecond=0)
        rapport = self.env["ir.actions.report"].with_context(lang="fr_CA")._render_qweb_html(
            "bf_hour_bank.action_report_hour_bank", banque.ids)[0].decode()
        self.assertIn("16,20", rapport)
        self.assertNotIn("16.20", rapport)
        local = pytz.utc.localize(avant).astimezone(pytz.timezone("America/Toronto"))
        lus = {(local + timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M") for m in (0, 1)}
        self.assertTrue(any(l in rapport for l in lus), "heure de Montréal attendue au PDF")
        nz = pytz.utc.localize(avant).astimezone(pytz.timezone("Pacific/Auckland"))
        self.assertNotIn(nz.strftime("%Y-%m-%d %H:%M"), rapport)
        self.assertNotIn(avant.strftime("%Y-%m-%d %H:%M"), rapport)
