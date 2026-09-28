# -*- coding: utf-8 -*-
"""l'adresse saisie sur les pages publiques n'est jamais un motif.

La recherche du contact se faisait par `=ilike` : `%` et `_` y sont des
jokers, et « %@domaine » trouvait le contact de quelqu'un d'autre — son
consentement au dossier, et le lien personnel qui l'attendait.
"""

import json
import re
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "bf_appointment", "bf_appointment_joker")
class TestCourrielSansJoker(HttpCase):

    def setUp(self):
        super().setUp()
        if not self.env["ir.module.module"].sudo().search_count(
                [("name", "=", "website"), ("state", "=", "installed")]):
            self.skipTest("module `website` absent : routes non routées")
        attendances = [
            Command.create({
                "name": "j%s" % d, "dayofweek": str(d), "hour_from": 0.0,
                "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        calendrier = self.env["resource.calendar"].create({
            "name": "24/7 joker", "attendance_ids": attendances, "tz": "UTC"})
        ressource = self.env["resource.resource"].create({
            "name": "joker material", "calendar_id": calendrier.id,
            "resource_type": "material", "tz": "UTC"})
        combinaison = self.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([ressource.id])]})
        self.notice = self.env.ref("privacy_consent.notice_recording")
        self.type_rdv = self.env["resource.booking.type"].create({
            "name": "Type joker", "slug": "type-joker",
            "duration": 1.0, "slot_duration": 1.0,
            "modifications_deadline": 0.0, "combination_assignment": "sorted",
            "resource_calendar_id": calendrier.id, "video_provider": "none",
            "requires_recording_consent": False,
            "recording_notice_id": self.notice.id,
            "sends_intake_acknowledgement": False,
            "is_public": True, "listed_on_landing": False,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": combinaison.id})],
        })
        self.victime = self.env["res.partner"].create({
            "name": "Victime", "email": "victime@joker.invalid"})
        self.lien = self.type_rdv._bf_create_onetime_link(self.victime)
        self.env.cr.flush()

    def _reserver(self, email):
        page = self.url_open("/appointment/%s" % self.type_rdv.slug)
        jeton = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
        envois = []

        def _envoi(rec, template, attach_ics=True, recipient=None):
            envois.append(rec.id)

        with patch.object(type(self.env["resource.booking"]),
                          "_send_appointment_email", _envoi):
            reponse = self.url_open(
                "/appointment/%s/book" % self.type_rdv.slug,
                data={"csrf_token": jeton.group(1), "name": "Intrus",
                      "email": email, "bf_consent": "on"},
            )
        return reponse, envois

    def test_l_adresse_publique_est_validee_strictement(self):
        from ..controllers.main import _bf_public_email
        self.assertEqual(_bf_public_email(" Jean@Client.COM "), "jean@client.com")
        self.assertFalse(_bf_public_email("a@b.c, d@e.fg"))
        self.assertFalse(_bf_public_email("Nom <a@b.com>"))
        self.assertFalse(_bf_public_email("pas-une-adresse"))

    def test_un_joker_ne_reprend_pas_le_lien_d_autrui(self):
        for joker in ("%@joker.invalid", "v_ctime@joker.invalid", "%ictime@joker.invalid"):
            reponse, envois = self._reserver(joker)
            self.assertNotIn(self.lien.access_token, reponse.url, joker)
            self.assertNotIn(self.lien.id, envois, joker)
        self.lien.invalidate_recordset()
        self.assertEqual(self.lien.partner_ids, self.victime)
        self.assertFalse(
            self.env["resource.booking"].search([
                ("type_id", "=", self.type_rdv.id),
                ("id", "!=", self.lien.id),
                ("partner_ids", "in", self.victime.ids)]),
            "aucune réservation neuve au nom de la victime")

    def _verifier_consentement(self, email):
        reponse = self.url_open(
            "/appointment/_consent_check",
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": {
                "email": email, "slug": self.type_rdv.slug}}),
            headers={"Content-Type": "application/json"},
        )
        return reponse.json()["result"]

    def test_la_verification_du_consentement_ne_sonde_pas_par_joker(self):
        self.env["privacy.consent"].sudo().create({
            "subject_partner_id": self.victime.id,
            "purpose_id": self.env.ref("privacy_consent.purpose_recording").id,
            "notice_id": self.notice.id,
            "status": "granted",
            "granted_at": fields.Datetime.now(),
        })
        self.env.cr.flush()
        self.assertTrue(
            self._verifier_consentement("victime@joker.invalid")["recording"]["active"],
            "témoin : l'adresse exacte retrouve bien le consentement")
        for joker in ("%@joker.invalid", "v_ctime@joker.invalid"):
            self.assertFalse(
                self._verifier_consentement(joker)["recording"]["active"], joker)
