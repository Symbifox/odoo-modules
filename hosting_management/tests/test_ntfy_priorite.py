"""Priorité ntfy d'une panne selon le service (préproduction, relais IMAP).

Un relais IMAP IDLE puis un banc de préproduction ont poussé en « urgent »
comme une prod client tombée.
Le courriel d'alerte reste ; la poussée suit ``health_ntfy_priority``.
"""
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNtfyPriorite(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partenaire = cls.env["res.partner"].create({"name": "Client ntfy"})
        cls.logiciel = cls.env["hosting.software"].create({"name": "Logiciel ntfy", "code": "NTFYPRIO"})

    def _service(self, nom, **vals):
        return self.env["hosting.service"].create(dict({
            "name": nom, "partner_id": self.partenaire.id,
            "software_id": self.logiciel.id,
            "server_url": "https://%s.banc.invalid" % nom,
        }, **vals))

    def _pousser(self, services, alert_type="down"):
        items = [{"service": s, "status": "degraded", "error": "x",
                  "response_time_ms": 10} for s in services]
        with patch.object(type(self.env["hosting.ntfy"]), "send") as envoi:
            self.env["hosting.service"]._send_ntfy_alert(items, alert_type=alert_type)
        return envoi

    def test_auto_selon_environnement(self):
        attendu = {"production": "urgent", "staging": "low",
                   "development": "low", "testing": "low"}
        for env_, prio in attendu.items():
            s = self._service("auto-" + env_, environment=env_)
            self.assertEqual(s.health_ntfy_priority, "auto")
            self.assertEqual(s._ntfy_priorite_panne(), prio, env_)

    def test_prod_urgente_preprod_discrete(self):
        prod = self._service("prod")
        banc = self._service("banc", environment="staging")
        self.assertEqual(self._pousser(prod).call_args.kwargs["priority"], "urgent")
        self.assertEqual(self._pousser(banc).call_args.kwargs["priority"], "low")

    def test_lot_mixte_sonne_pour_la_prod(self):
        prod = self._service("prod-mixte")
        banc = self._service("banc-mixte", environment="staging")
        envoi = self._pousser(prod | banc)
        self.assertEqual(envoi.call_args.kwargs["priority"], "urgent")
        self.assertIn("banc-mixte", envoi.call_args.kwargs["body"])

    def test_choix_force_prime_sur_l_environnement(self):
        relais = self._service("relais", health_ntfy_priority="low")
        self.assertEqual(self._pousser(relais).call_args.kwargs["priority"], "low")
        banc = self._service("banc-force", environment="staging",
                             health_ntfy_priority="high")
        self.assertEqual(self._pousser(banc).call_args.kwargs["priority"], "high")

    def test_aucune_poussee(self):
        muet = self._service("muet", health_ntfy_priority="none")
        self.assertFalse(self._pousser(muet).called)
        self.assertFalse(self._pousser(muet, "recovered").called)
        prod = self._service("prod-avec-muet")
        envoi = self._pousser(prod | muet)
        self.assertNotIn("muet", envoi.call_args.kwargs["body"].replace("prod-avec-muet", ""))
        self.assertIn("1", envoi.call_args.kwargs["title"])

    def test_retour_jamais_plus_fort_que_la_panne(self):
        prod = self._service("prod-retour")
        banc = self._service("banc-retour", environment="staging")
        self.assertEqual(self._pousser(prod, "recovered").call_args.kwargs["priority"],
                         "default")
        self.assertEqual(self._pousser(banc, "recovered").call_args.kwargs["priority"],
                         "low")
