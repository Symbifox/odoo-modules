"""La langue des textes que le serveur écrit pour la personne.

La source est en anglais et le français vient de i18n/fr_CA.po : chaque texte doit suivre la
langue de qui le lira, pas celle d'OdooBot ni celle de la source.
"""
import base64
import hashlib
import json
import os

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase

from ..models.bf_otp_device import PLAFOND_APPAREILS

BASE = "/bf_otp/mobile/v1"


def _defi(verificateur):
    return base64.urlsafe_b64encode(
        hashlib.sha256(verificateur.encode()).digest()).decode().rstrip("=")


@tagged("post_install", "-at_install")
class TestLangueAppareils(TransactionCase):

    VERIFICATEUR = "verificateur-langue-0123456789abcdefghij"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.personne = cls.env["res.users"].create({
            "name": "Banc langue", "login": "banc-otp-langue",
            "groups_id": [
                (4, cls.env.ref("base.group_user").id),
                (4, cls.env.ref("bf_otp.group_otp_user").id),
            ],
        })

    def _appareils(self, lang):
        A = self.env["bf.otp.device"].with_context(lang=lang)
        A._issue_pending(self.personne.id, challenge=_defi(self.VERIFICATEUR))
        return A.sudo().search([("user_id", "=", self.personne.id)], order="id desc", limit=1)

    def test_nom_par_defaut_de_l_appareil_dans_la_langue_de_qui_l_apparie(self):
        self.assertEqual(self._appareils("fr_CA").name, "Appareil Android")
        self.assertEqual(self._appareils("en_US").name, "Android device")

    def test_le_plafond_se_dit_dans_la_langue_de_la_personne(self):
        A = self.env["bf.otp.device"]
        for _ in range(PLAFOND_APPAREILS):
            code = A._issue_pending(self.personne.id, challenge=_defi(self.VERIFICATEUR))
            self.assertTrue(A._exchange(code, self.VERIFICATEUR))
        with self.assertRaises(UserError) as fr:
            A.with_context(lang="fr_CA")._issue_pending(
                self.personne.id, challenge=_defi(self.VERIFICATEUR))
        with self.assertRaises(UserError) as en:
            A.with_context(lang="en_US")._issue_pending(
                self.personne.id, challenge=_defi(self.VERIFICATEUR))
        self.assertIn("Trop d'appareils appariés (%s)" % PLAFOND_APPAREILS, str(fr.exception))
        self.assertIn("Too many paired devices (%s)" % PLAFOND_APPAREILS, str(en.exception))

    def test_le_refus_de_creation_se_dit_dans_la_langue_de_la_personne(self):
        A = self.env["bf.otp.device"].with_user(self.personne)
        with self.assertRaises(AccessError) as fr:
            A.with_context(lang="fr_CA").create({"user_id": self.personne.id})
        self.assertIn("ne se crée qu'en s'appariant", str(fr.exception))
        with self.assertRaises(AccessError) as en:
            A.with_context(lang="en_US").create({"user_id": self.personne.id})
        self.assertIn("can only be created by pairing", str(en.exception))


@tagged("post_install", "-at_install")
class TestLangueRoutesMobiles(HttpCase):

    def setUp(self):
        super().setUp()
        for code in ("en_US", "fr_CA"):
            self.env["res.lang"]._activate_lang(code)
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_otp.mobile_redirect_schemes", "com.bluefoxconsultant.otp://")

    def _personne(self, lang):
        login = f"banc-otp-langue-{lang}"
        personne = self.env["res.users"].create({
            "name": f"Banc {lang}", "login": login, "password": login, "lang": lang,
            "groups_id": [
                (4, self.env.ref("base.group_user").id),
                (4, self.env.ref("bf_otp.group_otp_user").id),
            ],
        })
        self.env["bf.otp.vault"].with_user(personne).create({
            "user_id": personne.id,
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "iterations": 600000,
            "verifier": base64.b64encode(os.urandom(40)).decode(),
            "verifier_iv": base64.b64encode(os.urandom(12)).decode(),
        })
        return login

    def _jeton(self, login):
        verificateur = "verificateur-de-banc-0123456789abcdef"
        self.authenticate(login, login)
        r = self.url_open(
            f"{BASE}/auth/start?redirect=com.bluefoxconsultant.otp://auth"
            f"&code_challenge={_defi(verificateur)}&code_challenge_method=S256",
            allow_redirects=False)
        code = r.headers["Location"].split("code=")[1].split("&")[0]
        return json.loads(self.url_open(
            f"{BASE}/auth/exchange",
            data=json.dumps({"code": code, "code_verifier": verificateur}),
            headers={"Content-Type": "application/json"}).content)["token"]

    def _porteur(self, login):
        """Le jeton de l'application, SANS la session du navigateur : le téléphone
        n'envoie ni cookie ni Accept-Language. Garder le cookie de session ferait
        passer l'essai par la langue de la session, pas par celle de la personne."""
        jeton = self._jeton(login)
        self.opener.cookies.pop("session_id", None)
        return {"Authorization": f"Bearer {jeton}", "Content-Type": "application/json"}

    def _cle_sans_nom(self, login):
        entetes = self._porteur(login)
        r = self.opener.post(
            f"{self.base_url()}{BASE}/credential/add",
            data=json.dumps({
                "credential_id": "Y3JlZC1kZS1iYW5jLTAxMjM0NTY3ODk",
                "prf_salt": "c2VsLWRlLWJhbmMtMDEyMzQ1Njc4OWFi",
                "wrapped_secret": "c2NlbGxlLWRlLWJhbmMtMDEyMzQ1Njc4OWFiY2RlZmdoaWprbG1ub3BxcnN0",
                "wrapped_iv": "dmVjdGV1cjEyb2N0",
            }),
            headers=entetes)
        self.assertEqual(200, r.status_code)
        return json.loads(r.content)["vault"]["credentials"][0]["name"]

    def _refus(self, login):
        r = self.opener.post(f"{self.base_url()}{BASE}/credential/remove",
                             data=json.dumps({"id": 0}), headers=self._porteur(login))
        self.assertEqual(400, r.status_code)
        return json.loads(r.content)["error"]

    def test_cle_posee_du_telephone_nommee_dans_la_langue_de_la_personne(self):
        """L'application n'envoie pas toujours de nom : le nom par défaut se lit
        ensuite dans le coffre, sur le site, par la personne elle-même."""
        self.assertEqual(self._cle_sans_nom(self._personne("fr_CA")), "Téléphone")
        self.assertEqual(self._cle_sans_nom(self._personne("en_US")), "Phone")

    def test_un_refus_renvoye_a_l_application_est_dans_la_langue_de_la_personne(self):
        """L'application affiche telle quelle l'erreur du serveur."""
        self.assertEqual(self._refus(self._personne("fr_CA")), "Clé d'accès introuvable.")
        self.assertEqual(self._refus(self._personne("en_US")), "Passkey not found.")

    def test_redirection_refusee_dans_la_langue_de_la_personne(self):
        """`/auth/start` est une route de personne connectée : le refus s'y lit."""
        for lang, texte in (("fr_CA", "Redirection non autorisée."), ("en_US", "Redirect not allowed.")):
            login = self._personne(lang)
            self.authenticate(login, login)
            r = self.url_open(f"{BASE}/auth/start?redirect=https://ailleurs.invalid/&code_challenge=x"
                              "&code_challenge_method=S256", allow_redirects=False)
            self.assertEqual(400, r.status_code)
            self.assertEqual(texte, r.text)
