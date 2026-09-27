"""Routes des préférences et annonce des capacités."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests.common import HttpCase, new_test_user, tagged

BASE = "/bf_bloc_notes/mobile/v1"


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestNoteCouleursHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="alice_http_couleurs", password="alice_http_couleurs",
                                  groups="base.group_user,project.group_project_user")

    def _authentifie(self):
        return patch(
            "odoo.addons.bf_bloc_notes.controllers.mobile_api._device",
            lambda: SimpleNamespace(user_id=self.alice, _fields={}),
        )

    def test_le_ping_annonce_les_capacites(self):
        charge = self.url_open(f"{BASE}/ping").json()
        self.assertEqual(charge["api"], 2)  # pièces jointes
        self.assertEqual(set(charge["features"]), {"color_hex", "tags", "prefs"})

    def test_prefs_exige_un_jeton(self):
        self.assertEqual(self.url_open(f"{BASE}/prefs").status_code, 401)

    def test_prefs_lire_puis_ecrire(self):
        with self._authentifie():
            charge = self.url_open(f"{BASE}/prefs").json()
            self.assertEqual(charge["prefs"]["layout"], "cards")
            reponse = self.url_open(f"{BASE}/prefs", data=json.dumps({"layout": "list"}),
                                    headers={"Content-Type": "application/json"})
            self.assertEqual(reponse.status_code, 200)
            self.assertEqual(reponse.json()["prefs"]["layout"], "list")
            refus = self.url_open(f"{BASE}/prefs", data=json.dumps({"layout": "x"}),
                                  headers={"Content-Type": "application/json"})
            self.assertEqual(refus.status_code, 400)
        self.assertEqual(self.alice.bf_note_layout, "list")

    def test_la_page_lit_et_ecrit_les_preferences(self):
        self.authenticate("alice_http_couleurs", "alice_http_couleurs")

        def rpc(**params):
            reponse = self.url_open("/notes/api/preferences", data=json.dumps({
                "jsonrpc": "2.0", "method": "call", "params": params,
            }), headers={"Content-Type": "application/json"})
            self.assertEqual(reponse.status_code, 200)
            return reponse.json()["result"]

        self.assertEqual(rpc()["density"], "comfortable")
        self.assertEqual(rpc(density="compact")["density"], "compact")
