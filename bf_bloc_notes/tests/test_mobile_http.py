"""Les deux portes du bloc-notes mobile, jouées par HTTP.

Le modèle est éprouvé dans `test_mobile`. Ici, seulement ce que les routes
ajoutent : l'authentification, la page et son agent de service, le contrat JSON,
et le retour en arrière quand un geste échoue à mi-chemin.

⚠️ Aucun module de messagerie n'est installé sur la base d'essai, donc aucun modèle
d'appareil n'existe : `_device` est remplacé par un double pour la moitié
authentifiée des essais de l'API à jeton (même approche que `bf_capture`).
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import HttpCase, new_test_user, tagged

from ..controllers.mobile_api import _appareil_acceptable

BASE = "/bf_bloc_notes/mobile/v1"


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestNotePage(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="alice_http_mobile",
                                  password="alice_http_mobile",
                                  groups="base.group_user,project.group_project_user",
                                  email="alice.http@example.com")

    def _rpc(self, route, **params):
        reponse = self.url_open(route, data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "params": params,
        }), headers={"Content-Type": "application/json"})
        self.assertEqual(reponse.status_code, 200)
        return reponse.json()["result"]

    # ── La page ──────────────────────────────────────────────────────

    def test_page_exige_une_session(self):
        reponse = self.url_open("/notes", allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("/web/login", reponse.headers.get("Location", ""))

    def test_page_sert_sa_coquille_sans_donnees_de_l_usager(self):
        """🔴 La coquille est gardée par l'agent de service, qui survit à la
        déconnexion : elle ne doit porter aucune note."""
        self.env["bf.note"].with_user(self.alice).create({
            "name": "Secret-mobile-titre", "body": "<p>Secret-mobile-corps</p>"})
        self.authenticate("alice_http_mobile", "alice_http_mobile")
        reponse = self.url_open("/notes")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn('rel="manifest" href="/notes/manifest.webmanifest"', reponse.text)
        self.assertIn('id="texte"', reponse.text)
        self.assertNotIn("Secret-mobile", reponse.text)
        self.assertNotIn("alice.http@example.com", reponse.text)

    def test_les_phrases_du_script_viennent_du_serveur(self):
        self.authenticate("alice_http_mobile", "alice_http_mobile")
        texte = self.url_open("/notes").text
        debut = texte.index('id="i18n">') + len('id="i18n">')
        mots = json.loads(texte[debut:texte.index("</script>", debut)])
        for cle in ("placeholder", "save", "saved_offline", "archive", "undo", "conflict"):
            self.assertTrue(mots.get(cle), cle)

    def test_manifeste_declare_le_partage_et_son_identite(self):
        manifeste = self.url_open("/notes/manifest.webmanifest").json()
        self.assertEqual(manifeste["id"], "/notes")
        self.assertEqual(manifeste["scope"], "/notes")
        self.assertEqual(manifeste["share_target"]["action"], "/notes")
        self.assertEqual(manifeste["share_target"]["params"]["text"], "texte")
        self.assertTrue(all("?v=" in icone["src"] for icone in manifeste["icons"]))
        self.assertTrue(any(i["purpose"] == "maskable" for i in manifeste["icons"]))

    def test_agent_de_service_peut_revendiquer_sa_portee(self):
        reponse = self.url_open("/notes/sw.js")
        self.assertEqual(reponse.headers.get("Service-Worker-Allowed"), "/notes")
        version = self.env["ir.module.module"].search(
            [("name", "=", "bf_bloc_notes")]).installed_version.replace(".", "-")
        self.assertIn("bf-notes-%s" % version, reponse.text)

    def test_les_icones_du_manifeste_existent(self):
        for icone in self.url_open("/notes/manifest.webmanifest").json()["icons"]:
            self.assertEqual(self.url_open(icone["src"]).status_code, 200, icone["src"])

    # ── Les routes JSON de la page ───────────────────────────────────

    def test_creer_puis_lister(self):
        self.authenticate("alice_http_mobile", "alice_http_mobile")
        cle = str(uuid.uuid4())
        cree = self._rpc("/notes/api/creer", client_uuid=cle, text="Depuis la page")
        self.assertTrue(cree["created"])
        rejoue = self._rpc("/notes/api/creer", client_uuid=cle, text="Depuis la page")
        self.assertFalse(rejoue["created"])
        liste = self._rpc("/notes/api/liste")
        self.assertEqual(liste["uid"], self.alice.id)
        self.assertIn(cree["note"]["id"], [n["id"] for n in liste["notes"]])

    def test_un_refus_revient_en_phrase(self):
        self.authenticate("alice_http_mobile", "alice_http_mobile")
        resultat = self._rpc("/notes/api/creer", client_uuid="pas-un-uuid", text="x")
        self.assertIn("error", resultat)

    def test_un_geste_qui_echoue_a_mi_chemin_n_ecrit_rien(self):
        """🔴 Une route JSON qui RENVOIE une erreur est commitée par Odoo : sans
        le retour en arrière, l'épingle posée avant l'échec resterait."""
        self.authenticate("alice_http_mobile", "alice_http_mobile")
        cree = self._rpc("/notes/api/creer", client_uuid=str(uuid.uuid4()), text="x")
        note = self.env["bf.note"].browse(cree["note"]["id"])
        appels = {"n": 0}

        def payload_qui_casse(enregistrement):
            appels["n"] += 1
            raise RuntimeError("panne simulée après l'écriture")

        with patch.object(type(note), "_mobile_payload", payload_qui_casse), \
                self.assertLogs("odoo.addons.bf_bloc_notes.controllers.page_pwa", "ERROR"):
            resultat = self._rpc("/notes/api/geste", id=note.id, action="pin")
        self.assertIn("error", resultat)
        self.assertEqual(appels["n"], 1)
        note.invalidate_recordset()
        self.assertFalse(note.pinned)


@tagged("post_install", "-at_install", "bf_bloc_notes")
class TestNoteMobileApi(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, login="alice_api_mobile",
                                  groups="base.group_user,project.group_project_user",
                                  email="alice.api@example.com")
        cls.bruno = new_test_user(cls.env, login="bruno_api_mobile",
                                  groups="base.group_user",
                                  email="bruno.api@example.com")

    def _authentifie(self, usager=None):
        usager = usager or self.alice
        return patch(
            "odoo.addons.bf_bloc_notes.controllers.mobile_api._device",
            lambda: SimpleNamespace(user_id=usager, _fields={}),
        )

    def _post(self, route, charge):
        return self.url_open(route, data=json.dumps(charge),
                             headers={"Content-Type": "application/json"})

    def test_ping_repond_sans_jeton(self):
        charge = self.url_open(f"{BASE}/ping").json()
        self.assertTrue(charge["enabled"])
        self.assertIn("task", charge["actions"])

    def test_sans_jeton_tout_le_reste_est_refuse(self):
        self.assertEqual(self.url_open(f"{BASE}/notes").status_code, 401)
        self.assertEqual(self._post(f"{BASE}/notes", {"client_uuid": str(uuid.uuid4()),
                                                      "text": "x"}).status_code, 401)
        self.assertEqual(self.url_open(f"{BASE}/projets").status_code, 401)

    def test_appareil_d_un_compte_portail_ou_archive_est_refuse(self):
        self.assertFalse(_appareil_acceptable(None))
        self.assertFalse(_appareil_acceptable(SimpleNamespace(
            user_id=SimpleNamespace(active=True, share=True))))
        self.assertFalse(_appareil_acceptable(SimpleNamespace(
            user_id=SimpleNamespace(active=False, share=False))))
        self.assertTrue(_appareil_acceptable(SimpleNamespace(
            user_id=SimpleNamespace(active=True, share=False))))

    def test_creer_rend_201_puis_200_au_renvoi(self):
        cle = str(uuid.uuid4())
        with self._authentifie():
            premier = self._post(f"{BASE}/notes", {"client_uuid": cle, "text": "Hors ligne"})
            second = self._post(f"{BASE}/notes", {"client_uuid": cle, "text": "Hors ligne"})
        self.assertEqual(premier.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(premier.json()["note"]["id"], second.json()["note"]["id"])
        self.assertEqual(self.env["bf.note"].search_count([("client_uuid", "=", cle)]), 1)

    def test_la_note_d_un_autre_est_introuvable(self):
        with self._authentifie(self.bruno):
            cree = self._post(f"{BASE}/notes", {"client_uuid": str(uuid.uuid4()),
                                                "text": "À Bruno"}).json()
        with self._authentifie():
            lue = self.url_open(f"{BASE}/notes/{cree['note']['id']}")
            geste = self._post(f"{BASE}/notes/{cree['note']['id']}/action", {"action": "archive"})
        self.assertEqual(lue.status_code, 404)
        self.assertEqual(geste.status_code, 404)
        self.assertTrue(self.env["bf.note"].browse(cree["note"]["id"]).active)

    def test_conflit_rend_409_et_la_note_du_serveur(self):
        with self._authentifie():
            cree = self._post(f"{BASE}/notes", {"client_uuid": str(uuid.uuid4()),
                                                "text": "v1"}).json()["note"]
            self._post(f"{BASE}/notes/{cree['id']}", {"text": "v2 ailleurs"})
            conflit = self._post(f"{BASE}/notes/{cree['id']}",
                                 {"text": "v2 ici", "write_date": cree["write_date"]})
        self.assertEqual(conflit.status_code, 409)
        self.assertEqual(conflit.json()["note"]["text"], "v2 ailleurs")

    def test_un_geste_rend_la_note_et_une_phrase(self):
        with self._authentifie():
            cree = self._post(f"{BASE}/notes", {"client_uuid": str(uuid.uuid4()),
                                                "text": "x"}).json()["note"]
            geste = self._post(f"{BASE}/notes/{cree['id']}/action", {"action": "pin"})
        self.assertEqual(geste.status_code, 200)
        self.assertTrue(geste.json()["note"]["pinned"])
        self.assertTrue(geste.json()["message"])

    def test_json_illisible_rend_400(self):
        with self._authentifie():
            reponse = self.url_open(f"{BASE}/notes", data=b"{pas du json",
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(reponse.json()["error"], "bad_request")

    def test_un_geste_refuse_a_mi_chemin_n_ecrit_rien(self):
        """🔴 Même garde que la page : la réponse d'erreur est commitée par
        Odoo, le retour en arrière est ce qui empêche l'écriture de rester."""
        with self._authentifie():
            cree = self._post(f"{BASE}/notes", {"client_uuid": str(uuid.uuid4()),
                                                "text": "x"}).json()["note"]
            note = self.env["bf.note"].browse(cree["id"])

            def payload_qui_casse(enregistrement):
                raise UserError("refus simulé après l'écriture")

            with patch.object(type(note), "_mobile_payload", payload_qui_casse):
                reponse = self._post(f"{BASE}/notes/{cree['id']}/action", {"action": "pin"})
        self.assertEqual(reponse.status_code, 400)
        note.invalidate_recordset()
        self.assertFalse(note.pinned)
