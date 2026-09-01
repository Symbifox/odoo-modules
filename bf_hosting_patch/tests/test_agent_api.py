# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""L'API de l'agent, éprouvée par le réseau et non par l'ORM.

Ce qu'on veut prouver ici n'est pas que le chemin heureux marche, c'est que les
chemins malheureux REFUSENT : un jeton absent, un jeton révoqué, un corps
illisible, un corps trop gros, une valeur de sélection inventée.
"""

import json
import uuid

from odoo.tests import HttpCase, tagged

BASE = "/symbifox/patch/v1"


@tagged("post_install", "-at_install")
class TestAgentApi(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Client d'API", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "api-01", "partner_id": cls.partner.id,
        })
        cls.endpoint.action_generate_enrol_code()
        cls.code = cls.endpoint.sudo().agent_enrol_code

    def _post(self, path, body, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.url_open(BASE + path, data=json.dumps(body).encode(),
                             headers=headers)

    def _mid(self):
        """Un machine-id neuf à chaque appel. Le banc porte de vraies machines :
        un identifiant en dur finit par heurter l'une d'elles, et le test
        échouerait pour une raison qui n'a rien à voir avec ce qu'il éprouve."""
        return uuid.uuid4().hex

    def _reports(self):
        """Compter les relevés DU poste éprouvé, jamais ceux de la base."""
        return self.env["bf.patch.report"].search_count(
            [("endpoint_id", "=", self.endpoint.id)]
        )

    def _system(self):
        return self.env["bf.patch.system"].search(
            [("endpoint_id", "=", self.endpoint.id)], limit=1
        )

    def _enrol(self, machine_id=None):
        machine_id = machine_id or self._mid()
        response = self._post("/enrol", {"code": self.code,
                                         "machine_id": machine_id})
        return response.json()["token"]

    # -- enrôlement ---------------------------------------------------
    def test_enrolement_rend_un_jeton(self):
        machine_id = self._mid()
        response = self._post("/enrol", {"code": self.code,
                                         "machine_id": machine_id})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["token"])
        self.endpoint.invalidate_recordset()
        self.assertEqual(self._system().machine_id, machine_id)

    def test_un_faux_code_est_refuse(self):
        response = self._post("/enrol", {"code": "n-importe-quoi",
                                         "machine_id": self._mid()})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])

    def test_un_code_ne_sert_qu_une_fois(self):
        self._enrol()
        response = self._post("/enrol", {"code": self.code,
                                         "machine_id": self._mid()})
        self.assertEqual(response.status_code, 403)

    # -- jeton --------------------------------------------------------
    def test_sans_jeton_le_releve_est_refuse(self):
        response = self._post("/report", {"pending_count": 3})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self._reports(), 0)

    def test_un_jeton_revoque_ne_vaut_plus_rien(self):
        token = self._enrol()
        self._system().action_revoke_agent()
        response = self._post("/report", {"pending_count": 3}, token=token)
        self.assertEqual(response.status_code, 401)

    def test_ping_reconnait_le_jeton(self):
        token = self._enrol()
        response = self.url_open(
            BASE + "/ping", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    # -- relevé -------------------------------------------------------
    def test_un_releve_complet_arrive_jusqu_a_la_fiche(self):
        token = self._enrol()
        response = self._post("/report", {
            "agent_version": "1.0.0",
            "os_release": "Ubuntu 26.04 LTS",
            "kernel_running": "7.0.0-29-generic",
            "kernel_installed": "7.0.0-30-generic",
            "reboot_required": True,
            "reboot_pending_since": "2026-08-21 06:43:54",
            "reboot_packages": "linux-base, linux-image-7.0.0-30-generic",
            "package_manager": "apt",
            "pending_count": 24,
            "pending_security_count": 0,
            "auto_update_mode": "security",
            "auto_update_detail": "les dépôts -updates ne sont pas autorisés",
            "disk_root_pct": 14,
            "disk_boot_pct": 14,
            "os_support_end": "2031-05-29",
            "os_support_state": "supported",
            "packages": [
                {"name": "base-files", "origin": "resolute-updates",
                 "version_installed": "14ubuntu6.1",
                 "version_candidate": "14ubuntu6.2", "is_security": False},
            ],
        }, token=token)
        self.assertEqual(response.status_code, 200)
        self.endpoint.invalidate_recordset()
        system = self._system()
        self.assertEqual(system.pending_count, 24)
        self.assertEqual(system.kernel_installed, "7.0.0-30-generic")
        self.assertEqual(system.auto_update_mode, "security")
        self.assertEqual(system.patch_state, "reboot")
        self.assertEqual(self.endpoint.patch_state, "reboot")
        report = self.env["bf.patch.report"].search(
            [("endpoint_id", "=", self.endpoint.id)]
        )
        self.assertEqual(len(report.package_ids), 1)
        self.assertEqual(report.package_ids.origin, "resolute-updates")

    def test_un_corps_illisible_est_refuse_sans_rien_ecrire(self):
        token = self._enrol()
        response = self.url_open(
            BASE + "/report", data=b"{ceci n'est pas du json",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._reports(), 0)

    def test_une_valeur_de_selection_inventee_retombe_sur_une_valeur_sure(self):
        """Une valeur inconnue venant du réseau ne doit ni lever ni s'écrire :
        elle retombe sur « inconnu », qui est la vérité."""
        token = self._enrol()
        response = self._post("/report", {
            "package_manager": "portage",
            "auto_update_mode": "peut-etre",
            "os_support_state": "bientot",
            "pending_count": 2,
        }, token=token)
        self.assertEqual(response.status_code, 200)
        self.endpoint.invalidate_recordset()
        system = self._system()
        self.assertFalse(system.package_manager)
        self.assertEqual(system.auto_update_mode, "unknown")
        self.assertEqual(system.os_support_state, "unknown")
        self.assertEqual(system.pending_count, 2)

    def test_un_corps_trop_gros_est_refuse(self):
        token = self._enrol()
        response = self._post("/report", {
            "pending_count": 1,
            "auto_update_detail": "x" * (600 * 1024),
        }, token=token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._reports(), 0)

    def test_l_agent_ne_peut_pas_creer_de_fiche_de_parc(self):
        """Le jeton authentifie une machine, et cette machine n'a qu'une fiche.
        Aucun champ du relevé ne doit pouvoir en faire naître une autre."""
        token = self._enrol()
        before = self.env["hosting.endpoint"].search_count([])
        self._post("/report", {
            "pending_count": 1,
            "endpoint_id": 999999,
            "name": "poste fantôme",
            "partner_id": self.partner.id,
        }, token=token)
        self.assertEqual(self.env["hosting.endpoint"].search_count([]), before)
        report = self.env["bf.patch.report"].search(
            [("endpoint_id", "=", self.endpoint.id)]
        )
        self.assertEqual(report.endpoint_id, self.endpoint)
        self.assertEqual(report.system_id, self._system())
