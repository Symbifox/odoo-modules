"""La page d'accord à l'appariement.

``/auth/start`` émettait le code d'appariement sur un simple GET authentifié et
repartait aussitôt vers le schéma de l'app : une app tierce qui déclare ce
schéma appariait un appareil dans le navigateur de la personne, avec SON défi
PKCE, sans qu'elle voie rien. Le GET ne rend plus qu'une page ; seul un POST
« Autoriser », jeton CSRF compris, émet le code.
"""
import base64
import hashlib
import html
import re
import secrets
import urllib.parse

from odoo.tests import HttpCase, new_test_user, tagged

API = "/bf_nfc/mobile/v1"
SCHEMA = "com.bluefoxconsultant.pastilles://auth"


@tagged("post_install", "-at_install")
class TestAccordAppariement(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.personne = new_test_user(cls.env, login="accord_appariement", groups="base.group_user,bf_nfc.group_nfc_user")
        cls.Device = cls.env["bf.nfc.device"].sudo().with_context(active_test=False)

    def _en_attente(self):
        return self.Device.search_count([("user_id", "=", self.personne.id)])

    def _depart(self, **extra):
        self.authenticate("accord_appariement", "accord_appariement")
        verificateur = secrets.token_urlsafe(32)
        defi = base64.urlsafe_b64encode(
            hashlib.sha256(verificateur.encode()).digest()).decode().rstrip("=")
        valeurs = {"redirect": SCHEMA, "state": "etat-9", "code_challenge": defi,
                   "code_challenge_method": "S256", "device_name": "Pixel de banc"}
        valeurs.update(extra)
        return self.url_open(API + "/auth/start?" + urllib.parse.urlencode(valeurs),
                             allow_redirects=False)

    @staticmethod
    def _champs(page):
        return {k: html.unescape(v) for k, v in
                re.findall(r'name="([a-z_]+)" value="([^"]*)"', page)}

    def test_le_get_rend_la_page_et_n_emet_rien(self):
        avant = self._en_attente()
        reponse = self._depart()
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(reponse.headers.get("Location"))
        self.assertEqual(self._en_attente(), avant, "Un code a été émis sans accord.")
        self.assertNotIn("code=", reponse.text)
        self.assertIn("Pixel de banc", reponse.text)
        self.assertTrue(self._champs(reponse.text).get("csrf_token"))
        self.assertEqual(reponse.headers.get("X-Frame-Options"), "DENY")

    def test_le_nom_d_appareil_est_echappe(self):
        reponse = self._depart(device_name="<img src=x onerror=alert(1)>")
        self.assertNotIn("<img", reponse.text)

    def test_un_post_sans_jeton_csrf_est_refuse(self):
        champs = self._champs(self._depart().text)
        champs.pop("csrf_token")
        reponse = self.url_open(API + "/auth/consent", data=dict(champs, decision="allow"),
                                allow_redirects=False)
        self.assertNotIn(reponse.status_code, (302, 303))
        self.assertEqual(self._en_attente(), 0)

    def test_refuser_n_emet_rien(self):
        champs = self._champs(self._depart().text)
        reponse = self.url_open(API + "/auth/consent", data=dict(champs, decision="deny"),
                                allow_redirects=False)
        self.assertEqual(reponse.status_code, 303)
        self.assertIn("error=access_denied", reponse.headers["Location"])
        self.assertEqual(self._en_attente(), 0)

    def test_autoriser_emet_le_code(self):
        champs = self._champs(self._depart().text)
        reponse = self.url_open(API + "/auth/consent", data=dict(champs, decision="allow"),
                                allow_redirects=False)
        self.assertEqual(reponse.status_code, 303)
        self.assertTrue(reponse.headers["Location"].startswith(SCHEMA + "?"))
        self.assertIn("code=", reponse.headers["Location"])
        self.assertIn("state=etat-9", reponse.headers["Location"])

    def test_les_champs_caches_sont_revalides(self):
        champs = self._champs(self._depart().text)
        champs["redirect"] = "https://ailleurs.example/vol"
        reponse = self.url_open(API + "/auth/consent", data=dict(champs, decision="allow"),
                                allow_redirects=False)
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(self._en_attente(), 0)
