# -*- coding: utf-8 -*-
"""Une écriture concurrente sur une fiche de service ne coûte plus la passe.

Ce que ces essais tiennent en place :

* une mesure heurtée par une écriture concurrente est rejouée, et enregistrée UNE
  fois : la mesure de la tentative heurtée ne survit pas à côté de la bonne ;
* les autres services de la passe gardent leur mesure ;
* une mesure heurtée à chaque tentative est abandonnée seule, sans lever : la passe
  continue, et l'abandon se lit au journal ;
* la bascule d'alerte survit à la reprise : une panne confirmée sur une tentative
  rejouée alerte toujours.

Le heurt est simulé en levant SerializationFailure APRÈS l'écriture de la mesure,
là où le flush de last_health_check le lève en production. La vraie collision
(second curseur qui commite pendant la passe) se joue hors de ces essais : un
curseur de test ne peut ni commiter ni annuler.
"""

from unittest.mock import patch

from psycopg2.errors import SerializationFailure

from odoo.tests.common import TransactionCase, tagged

MODULE = "odoo.addons.hosting_management.models.hosting_service"


@tagged("post_install", "-at_install")
class TestSanteConcurrence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Service = cls.env["hosting.service"]
        Service.search([("state", "=", "active")]).write({"state": "suspended"})
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("hosting.health_alert_threshold", "1")
        ICP.set_param("hosting.health_alert_min_consecutive", "1")
        logiciel = cls.env["hosting.software"].create({
            "name": "Odoo de banc (concurrence)",
            "code": "TCONC",
            "software_type": "self_hosted",
        })
        client = cls.env["res.partner"].create({"name": "Client de banc"})
        cls.heurte, cls.voisin = Service.create([{
            "name": nom,
            "partner_id": client.id,
            "software_id": logiciel.id,
            "server_url": url,
            "state": "active",
        } for nom, url in (
            ("Service heurté", "https://heurte.example.invalid"),
            ("Service voisin", "https://voisin.example.invalid"),
        )])

    def _mesures(self, service):
        return self.env["hosting.health.check"].search_count([("service_id", "=", service.id)])

    def _cycle(self, statut, code, heurts):
        """Un passage du cron ; les `heurts` premières écritures du service heurté lèvent."""
        Service = type(self.env["hosting.service"])
        origine = Service._sante_enregistrer
        appels = []

        def enregistrer(this, service, mesure, min_consecutive):
            bascule = origine(this, service, mesure, min_consecutive)
            if service == self.heurte:
                appels.append(1)
                if len(appels) <= heurts:
                    raise SerializationFailure("could not serialize access due to concurrent update")
            return bascule

        with patch.object(Service, "_do_health_check",
                          staticmethod(lambda *a, **k: (statut, 40, code, None))), \
             patch.object(Service, "_sante_enregistrer", enregistrer), \
             patch.object(Service, "_is_in_maintenance_window", return_value=False), \
             patch.object(Service, "_send_health_alert_email") as courriel, \
             patch.object(Service, "_send_ntfy_alert"), \
             patch(f"{MODULE}.time.sleep"):
            self.env["hosting.service"]._cron_health_check()
        return courriel, len(appels)

    def test_mesure_heurtee_rejouee_une_seule_fois(self):
        _courriel, tentatives = self._cycle("up", 200, heurts=1)
        self.assertEqual(tentatives, 2)
        self.assertEqual(self._mesures(self.heurte), 1,
                         "la mesure de la tentative heurtée ne doit pas survivre")
        self.assertEqual(self._mesures(self.voisin), 1)
        self.assertEqual(self.heurte.last_health_status, "up")

    def test_heurt_persistant_abandonne_seul(self):
        with self.assertLogs(MODULE, level="WARNING") as journal:
            _courriel, tentatives = self._cycle("up", 200, heurts=99)
        self.assertEqual(tentatives, 3)
        self.assertEqual(self._mesures(self.heurte), 0)
        self.assertEqual(self._mesures(self.voisin), 1,
                         "un service heurté ne doit pas coûter la mesure des autres")
        self.assertTrue(any("abandonnée" in ligne for ligne in journal.output))

    def test_panne_confirmee_apres_reprise_alerte(self):
        courriel, _tentatives = self._cycle("degraded", 502, heurts=1)
        self.assertTrue(self.heurte.health_alert_active)
        envois = [c for c in courriel.call_args_list if c.kwargs.get("alert_type") == "down"]
        self.assertEqual(len(envois), 1)
        alertes = {item["service"] for item in envois[0].args[0]}
        self.assertIn(self.heurte, alertes)
        self.assertEqual(self._mesures(self.heurte), 1)
