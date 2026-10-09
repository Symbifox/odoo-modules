# -*- coding: utf-8 -*-
"""Le silence d'alerte posé pendant une mise à jour de conteneur.

Ce que ces essais tiennent en place :

* un 502 pendant le silence n'alerte pas, mais il est ENREGISTRÉ : un silence
  qui avale aussi l'historique cacherait la durée réelle des poses ;
* 🔴 si le silence expire sur une panne, l'alerte part au cycle suivant, avec
  les échecs du silence dans le compte. Sinon une pose ratée serait masquée ;
* un outil tué avant de lever le silence ne masque rien plus d'une heure.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSilencePose(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Service = cls.env["hosting.service"]
        Service.search([("state", "=", "active")]).write({"state": "suspended"})
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("hosting.health_alert_threshold", "1")
        ICP.set_param("hosting.health_alert_min_consecutive", "2")
        logiciel = cls.env["hosting.software"].create({
            "name": "Odoo de banc (silence)",
            "code": "TSIL",
            "software_type": "self_hosted",
        })
        cls.service = Service.create({
            "name": "Locataire de banc",
            "partner_id": cls.env["res.partner"].create({"name": "Client de banc"}).id,
            "software_id": logiciel.id,
            "server_url": "https://banc.example.invalid",
            "docker_container": "banc-silence-odoo",
            "state": "active",
        })

    def _cycle(self, statut, code):
        """Un passage du cron, la sonde HTTP remplacée, les envois interceptés."""
        Service = type(self.env["hosting.service"])
        with patch.object(Service, "_do_health_check",
                          staticmethod(lambda *a, **k: (statut, 40, code, None))), \
             patch.object(Service, "_is_in_maintenance_window", return_value=False), \
             patch.object(Service, "_send_health_alert_email") as courriel, \
             patch.object(Service, "_send_ntfy_alert") as ntfy:
            self.env["hosting.service"]._cron_health_check()
        return courriel, ntfy

    def _envoye(self, courriel, alert_type):
        return any(c.kwargs.get("alert_type") == alert_type for c in courriel.call_args_list)

    def test_sans_silence_deux_echecs_alertent(self):
        """Le témoin : sans silence, le comportement d'avant tient."""
        self._cycle("degraded", 502)
        courriel, ntfy = self._cycle("degraded", 502)
        self.assertTrue(self._envoye(courriel, "down"))
        self.assertTrue(ntfy.called)
        self.assertTrue(self.service.health_alert_active)

    def test_silence_enregistre_sans_alerter(self):
        codes = self.env["hosting.service"].suspendre_alertes_conteneur("banc-silence-odoo", 15)
        self.assertEqual(codes, [self.service.code])
        for _ in range(3):
            courriel, ntfy = self._cycle("degraded", 502)
            self.assertFalse(self._envoye(courriel, "down"))
            self.assertFalse(ntfy.called)
        self.assertFalse(self.service.health_alert_active)
        self.assertEqual(
            self.env["hosting.health.check"].search_count([("service_id", "=", self.service.id)]), 3,
            "les contrôles du silence doivent rester dans l'historique")

    def test_silence_expire_sur_une_panne_alerte(self):
        self.env["hosting.service"].suspendre_alertes_conteneur("banc-silence-odoo", 15)
        self._cycle("degraded", 502)
        self._cycle("degraded", 502)
        self.service.health_silence_until = fields.Datetime.now() - timedelta(seconds=1)
        courriel, ntfy = self._cycle("degraded", 502)
        self.assertTrue(self._envoye(courriel, "down"), "la pose ratée doit alerter à l'expiration")
        self.assertTrue(ntfy.called)

    def test_retour_pendant_le_silence_sans_avis_de_retablissement(self):
        self.env["hosting.service"].suspendre_alertes_conteneur("banc-silence-odoo", 15)
        self._cycle("degraded", 502)
        courriel, ntfy = self._cycle("up", 200)
        self.assertFalse(courriel.called)
        self.assertFalse(ntfy.called)

    def test_plafond_et_levee(self):
        Service = self.env["hosting.service"]
        Service.suspendre_alertes_conteneur("banc-silence-odoo", 600)
        self.assertLessEqual(
            self.service.health_silence_until,
            fields.Datetime.now() + timedelta(minutes=Service.SILENCE_MAX_MINUTES, seconds=5))
        Service.suspendre_alertes_conteneur("banc-silence-odoo", 0)
        self.assertFalse(self.service.health_silence_until)
        self.assertEqual(Service.suspendre_alertes_conteneur("inconnu-odoo", 10), [])
