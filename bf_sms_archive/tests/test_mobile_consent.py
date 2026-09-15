# -*- coding: utf-8 -*-
"""La page d'accord à l'appariement (audit du 2026-09-08, S-M1).

Ce qui se joue : ``/auth/start`` émettait le code d'appariement sur un simple
GET authentifié et repartait aussitôt vers le schéma de l'app. Une app tierce
du téléphone qui déclare ce schéma pouvait ouvrir l'URL dans le navigateur où
la personne est connectée, avec SON défi PKCE, et apparier un appareil sans
qu'elle voie rien. PKCE ne protège pas contre l'initiateur.

Chaque essai porte la valeur qui ferait passer l'ancienne route : un GET qui
rend un code, un POST sans jeton CSRF, un refus qui émettrait quand même, des
champs cachés qu'on ne revérifierait pas.
"""
import base64
import hashlib
import json
import re
import secrets

from odoo.tests import HttpCase, new_test_user, tagged

BASE = "/bf_sms_archive/mobile/v1"
SCHEMA = "com.bluefoxconsultant.sms://auth"


def _pkce():
    verificateur = secrets.token_urlsafe(32)
    defi = base64.urlsafe_b64encode(
        hashlib.sha256(verificateur.encode("utf-8")).digest()
    ).decode().rstrip("=")
    return verificateur, defi


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestMobileConsent(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Les deux langues actives : une base montée en fr_CA seul n'a pas
        # en_US, et `get_lang` y ramène tout le monde au français.
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.user = new_test_user(
            cls.env, login="sms_accord_user", name="Usagère Accord", lang="en_US",
            groups="base.group_user,bf_sms_archive.group_sms_user")
        cls.francophone = new_test_user(
            cls.env, login="sms_accord_fr", name="Usager Francophone",
            lang="fr_CA",
            groups="base.group_user,bf_sms_archive.group_sms_user")
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_sms_archive.mobile_redirect_schemes", "com.bluefoxconsultant.sms://")
        cls.Device = cls.env["sms.archive.mobile.device"]

    def _en_attente(self):
        return self.Device.sudo().search_count([("pending_code", "!=", False)])

    def _ouvrir(self, defi, login="sms_accord_user", **extra):
        self.authenticate(login, login)
        params = {"redirect": SCHEMA, "state": "etat-42", "code_challenge": defi,
                  "code_challenge_method": "S256", **extra}
        query = "&".join("%s=%s" % (k, v) for k, v in params.items())
        return self.url_open(BASE + "/auth/start?" + query,
                             timeout=30, allow_redirects=False)

    @staticmethod
    def _champs(page):
        return dict(re.findall(r'name="([a-z_]+)" value="([^"]*)"', page))

    def _repondre(self, champs, decision):
        return self.url_open(BASE + "/auth/consent",
                             data=dict(champs, decision=decision),
                             timeout=30, allow_redirects=False)

    # ---------------------------------------------------------------- GET
    def test_le_get_rend_la_page_et_aucun_code(self):
        _verificateur, defi = _pkce()
        avant = self._en_attente()
        reponse = self._ouvrir(defi, device_name="Pixel%20de%20banc")
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(reponse.headers.get("Location"), "Le GET redirige encore.")
        self.assertEqual(self._en_attente(), avant,
                         "Un code d'appariement a été émis sans accord.")
        page = reponse.text
        self.assertNotIn("code=", page)
        self.assertIn("Symbifox Mobile", page)
        self.assertIn("Usagère Accord", page)
        self.assertIn("sms_accord_user", page)
        self.assertIn("Pixel de banc", page)
        champs = self._champs(page)
        self.assertTrue(champs.get("csrf_token"))
        self.assertEqual(champs["state"], "etat-42")
        self.assertIn('action="%s/auth/consent"' % BASE, page)

    def test_la_page_ne_s_encadre_pas_et_ne_se_garde_pas(self):
        _verificateur, defi = _pkce()
        reponse = self._ouvrir(defi)
        self.assertEqual(reponse.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'",
                      reponse.headers.get("Content-Security-Policy", ""))
        self.assertIn("no-store", reponse.headers.get("Cache-Control", ""))

    def test_le_nom_d_appareil_est_echappe(self):
        _verificateur, defi = _pkce()
        reponse = self._ouvrir(
            defi, device_name="%3Cscript%3Ealert(1)%3C/script%3E%22%3E")
        self.assertNotIn("<script>", reponse.text)
        self.assertIn("&lt;script&gt;", reponse.text)

    def test_la_page_parle_la_langue_de_l_usager(self):
        _verificateur, defi = _pkce()
        anglais = self._ouvrir(defi).text
        self.assertIn(">Allow<", anglais)
        self.assertIn(">Deny<", anglais)
        francais = self._ouvrir(defi, login="sms_accord_fr").text
        self.assertIn(">Autoriser<", francais)
        self.assertIn(">Refuser<", francais)
        self.assertIn("messages SMS", francais)

    def test_les_erreurs_de_validation_reviennent_par_le_lien_profond(self):
        """Jamais une page sans issue : l'app enchaîne les deux modules."""
        reponse = self._ouvrir("")
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("error=pkce_required", reponse.headers["Location"])

    # --------------------------------------------------------------- POST
    def test_un_post_sans_jeton_csrf_est_refuse(self):
        _verificateur, defi = _pkce()
        champs = self._champs(self._ouvrir(defi).text)
        champs.pop("csrf_token")
        avant = self._en_attente()
        reponse = self._repondre(champs, "allow")
        self.assertEqual(reponse.status_code, 400)
        self.assertNotIn("code=", reponse.headers.get("Location", ""))
        self.assertEqual(self._en_attente(), avant)

    def test_refuser_rend_access_denied_par_le_lien_profond(self):
        _verificateur, defi = _pkce()
        champs = self._champs(self._ouvrir(defi).text)
        avant = self._en_attente()
        reponse = self._repondre(champs, "deny")
        self.assertIn(reponse.status_code, (302, 303))
        emplacement = reponse.headers["Location"]
        self.assertTrue(emplacement.startswith(SCHEMA))
        self.assertIn("error=access_denied", emplacement)
        self.assertIn("state=etat-42", emplacement)
        self.assertNotIn("code=", emplacement)
        self.assertEqual(self._en_attente(), avant)

    def test_une_decision_inconnue_vaut_un_refus(self):
        _verificateur, defi = _pkce()
        champs = self._champs(self._ouvrir(defi).text)
        reponse = self._repondre(champs, "peut-etre")
        self.assertIn("error=access_denied", reponse.headers["Location"])

    def test_autoriser_rend_un_code_qui_s_echange_avec_le_verificateur(self):
        verificateur, defi = _pkce()
        champs = self._champs(self._ouvrir(defi, device_name="Pixel").text)
        reponse = self._repondre(champs, "allow")
        self.assertIn(reponse.status_code, (302, 303))
        emplacement = reponse.headers["Location"]
        self.assertTrue(emplacement.startswith(SCHEMA))
        self.assertIn("state=etat-42", emplacement)
        code = re.search(r"[?&]code=([^&]+)", emplacement).group(1)
        echange = self.url_open(
            BASE + "/auth/exchange",
            data=json.dumps({"code": code, "code_verifier": verificateur}).encode(),
            headers={"Content-Type": "application/json"}, timeout=30)
        self.assertEqual(echange.status_code, 200, echange.text)
        corps = echange.json()
        self.assertTrue(corps["token"])
        self.assertEqual(corps["user_id"], self.user.id)
        appareil = self.Device._resolve(corps["token"])
        self.assertEqual(appareil.name, "Pixel")

    def test_le_post_revalide_la_redirection(self):
        """Les champs cachés viennent du navigateur : une redirection étrangère
        glissée dans le formulaire ne doit pas recevoir de code."""
        _verificateur, defi = _pkce()
        champs = self._champs(self._ouvrir(defi).text)
        champs["redirect"] = "https://malveillant.test/vol"
        avant = self._en_attente()
        reponse = self._repondre(champs, "allow")
        self.assertEqual(reponse.status_code, 400)
        self.assertNotIn("code=", reponse.headers.get("Location", ""))
        self.assertEqual(self._en_attente(), avant)

    def test_le_post_revalide_pkce(self):
        _verificateur, defi = _pkce()
        champs = self._champs(self._ouvrir(defi).text)
        champs["code_challenge_method"] = "plain"
        avant = self._en_attente()
        reponse = self._repondre(champs, "allow")
        self.assertIn("error=pkce_required", reponse.headers["Location"])
        self.assertEqual(self._en_attente(), avant)

    def test_le_post_revalide_le_groupe(self):
        """L'usager vient de la session : un compte sans le groupe SMS qui
        rejoue le formulaire d'un autre n'obtient rien."""
        _verificateur, defi = _pkce()
        champs = self._champs(self._ouvrir(defi).text)
        new_test_user(self.env, login="sms_accord_sans_groupe",
                      groups="base.group_user")
        self.authenticate("sms_accord_sans_groupe", "sms_accord_sans_groupe")
        # Le jeton CSRF de l'autre session ne vaut rien ici : on prend celui de
        # la nouvelle, pour éprouver le groupe et non la protection CSRF.
        page = self.url_open("/web", timeout=30)
        jeton = re.search(r'csrf_token: "([^"]+)"', page.text)
        self.assertTrue(jeton, "Le banc n'a pas trouvé le jeton CSRF de /web.")
        champs["csrf_token"] = jeton.group(1)
        avant = self._en_attente()
        reponse = self._repondre(champs, "allow")
        self.assertEqual(reponse.status_code, 403)
        self.assertNotIn("code=", reponse.headers.get("Location", ""))
        self.assertEqual(self._en_attente(), avant)
