"""L'API mobile : ce qu'elle accorde, et surtout ce qu'elle refuse."""
import json

from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase

BASE = "/bf_otp/mobile/v1"


class TestAppariement(TransactionCase):
    """Le cycle du code à usage unique, sans passer par HTTP."""

    def setUp(self):
        super().setUp()
        self.Appareil = self.env["bf.otp.device"]
        self.personne = self.env["res.users"].create({
            "name": "Banc", "login": "banc-otp-api",
            "groups_id": [(4, self.env.ref("bf_otp.group_otp_user").id)],
        })

    def test_le_code_ne_sert_qu_une_fois(self):
        code = self.Appareil._issue_pending(self.personne.id)
        premier = self.Appareil._exchange(code)
        self.assertTrue(premier, "le premier échange devrait réussir")
        self.assertTrue(premier.sudo().device_token)
        # 🔴 Le second doit échouer. Un code qui survit à son échange est une
        # clé réutilisable dès qu'il traîne dans un historique de navigateur.
        self.assertFalse(self.Appareil._exchange(code))

    def test_le_code_est_efface_par_l_echange(self):
        code = self.Appareil._issue_pending(self.personne.id)
        appareil = self.Appareil._exchange(code)
        self.assertFalse(appareil.sudo().pending_code)
        self.assertFalse(appareil.sudo().pending_code_expiry)

    def test_un_code_perime_est_refuse_et_l_appareil_jete(self):
        from datetime import timedelta

        from odoo import fields
        code = self.Appareil._issue_pending(self.personne.id)
        attente = self.Appareil.sudo().search([("pending_code", "=", code)])
        attente.write({
            "pending_code_expiry": fields.Datetime.now() - timedelta(minutes=1),
        })
        self.assertFalse(self.Appareil._exchange(code))
        self.assertFalse(attente.exists(),
                         "un appariement périmé ne doit pas rester en base")

    def test_un_code_inconnu_est_refuse_sans_lever(self):
        for mauvais in ("", None, "pas-un-code", "x" * 64):
            self.assertFalse(self.Appareil._exchange(mauvais))

    def test_le_jeton_resout_l_appareil_et_pas_un_autre(self):
        code = self.Appareil._issue_pending(self.personne.id)
        appareil = self.Appareil._exchange(code)
        jeton = appareil.sudo().device_token
        self.assertEqual(self.Appareil._resolve(jeton), appareil)
        self.assertFalse(self.Appareil._resolve(jeton + "x"))
        self.assertFalse(self.Appareil._resolve(""))

    def test_un_appareil_desactive_ne_resout_plus(self):
        code = self.Appareil._issue_pending(self.personne.id)
        appareil = self.Appareil._exchange(code)
        jeton = appareil.sudo().device_token
        appareil.sudo().write({"active": False})
        self.assertFalse(self.Appareil._resolve(jeton),
                         "se déconnecter doit vraiment couper l'accès")

    def test_la_purge_epargne_les_appareils_apparies(self):
        from datetime import timedelta

        from odoo import fields
        # Un appariement abandonné, périmé.
        abandonne = self.Appareil._issue_pending(self.personne.id)
        self.Appareil.sudo().search([("pending_code", "=", abandonne)]).write({
            "pending_code_expiry": fields.Datetime.now() - timedelta(hours=1),
        })
        # Un appareil bel et bien apparié.
        vivant = self.Appareil._exchange(
            self.Appareil._issue_pending(self.personne.id))

        self.Appareil._purger_codes_perimes()
        self.assertTrue(vivant.exists(),
                        "la purge ne doit pas déconnecter un téléphone qui marche")
        self.assertFalse(
            self.Appareil.sudo().search([("pending_code", "=", abandonne)]))


@tagged("post_install", "-at_install")
class TestRoutesMobiles(HttpCase):

    def setUp(self):
        super().setUp()
        self.personne = self.env["res.users"].create({
            "name": "Banc HTTP", "login": "banc-otp-http",
            "password": "banc-otp-http",
            "groups_id": [
                (4, self.env.ref("base.group_user").id),
                (4, self.env.ref("bf_otp.group_otp_user").id),
            ],
        })
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_otp.mobile_redirect_schemes", "com.bluefoxconsultant.otp://")

    def _json(self, reponse):
        return json.loads(reponse.content.decode())

    def test_ping_est_public_et_porte_la_marque(self):
        # L'application s'habille AVANT toute connexion : cette route doit
        # répondre sans session.
        r = self.url_open(f"{BASE}/ping")
        self.assertEqual(r.status_code, 200)
        corps = self._json(r)
        self.assertTrue(corps["ok"])
        self.assertEqual(corps["module"], "bf_otp")
        self.assertIn("branding", corps)
        self.assertIn("name", corps["branding"])
        # ⚠️ Rien de sensible ne doit voyager sur une route publique.
        brut = r.content.decode().lower()
        for interdit in ("secret", "cipher", "token", "password"):
            self.assertNotIn(interdit, brut,
                             f"« {interdit} » n'a rien à faire dans /ping")

    def test_auth_start_refuse_une_redirection_etrangere(self):
        # 🔴 Le contrôle qui empêche la redirection ouverte : sans lui, cette
        # route remet un code d'échange vivant à l'URL que l'appelant nomme.
        self.authenticate("banc-otp-http", "banc-otp-http")
        r = self.url_open(
            f"{BASE}/auth/start?redirect=https://exemple-mechant.com/vol",
            allow_redirects=False)
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("code=", r.headers.get("Location", ""))

    def test_auth_start_accepte_le_schema_de_l_application(self):
        self.authenticate("banc-otp-http", "banc-otp-http")
        r = self.url_open(
            f"{BASE}/auth/start?redirect=com.bluefoxconsultant.otp://auth&state=xyz",
            allow_redirects=False)
        self.assertEqual(r.status_code, 302)
        cible = r.headers["Location"]
        self.assertTrue(cible.startswith("com.bluefoxconsultant.otp://auth"))
        self.assertIn("code=", cible)
        self.assertIn("state=xyz", cible)

    def test_auth_start_sans_session_mene_a_la_connexion(self):
        # `auth="user"` : pas de session, donc Odoo envoie vers sa page de
        # connexion, qui mène elle-même à Authentik. C'est ce qui rend
        # l'ouverture transparente quand la session existe déjà.
        r = self.url_open(
            f"{BASE}/auth/start?redirect=com.bluefoxconsultant.otp://auth",
            allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertIn("/web/login", r.headers.get("Location", ""))

    def test_les_routes_du_coffre_refusent_sans_jeton(self):
        for chemin in ("/vault", "/tokens"):
            r = self.url_open(f"{BASE}{chemin}")
            self.assertEqual(r.status_code, 401, chemin)
        r = self.url_open(f"{BASE}/logout", data="{}")
        self.assertEqual(r.status_code, 401)

    def test_un_jeton_inconnu_est_refuse(self):
        r = self.opener.get(
            f"{self.base_url()}{BASE}/vault",
            headers={"Authorization": "Bearer pas-un-jeton"})
        self.assertEqual(r.status_code, 401)

    def test_le_parcours_complet_rend_le_coffre_chiffre(self):
        self.authenticate("banc-otp-http", "banc-otp-http")
        r = self.url_open(
            f"{BASE}/auth/start?redirect=com.bluefoxconsultant.otp://auth",
            allow_redirects=False)
        code = r.headers["Location"].split("code=")[1].split("&")[0]

        r = self.url_open(f"{BASE}/auth/exchange",
                          data=json.dumps({"code": code}),
                          headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200)
        jeton = self._json(r)["token"]
        self.assertTrue(jeton)

        entetes = {"Authorization": f"Bearer {jeton}"}
        r = self.opener.get(f"{self.base_url()}{BASE}/vault", headers=entetes)
        self.assertEqual(r.status_code, 200)
        # Pas de coffre pour ce compte de banc : `None`, pas une erreur.
        self.assertIsNone(self._json(r)["vault"])
        self.assertIn("branding", self._json(r))

        r = self.opener.get(f"{self.base_url()}{BASE}/tokens", headers=entetes)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._json(r)["tokens"], [])

        # Se déconnecter coupe vraiment.
        r = self.opener.post(f"{self.base_url()}{BASE}/logout", headers=entetes)
        self.assertEqual(r.status_code, 200)
        r = self.opener.get(f"{self.base_url()}{BASE}/vault", headers=entetes)
        self.assertEqual(r.status_code, 401)

    def test_les_jetons_rendus_sont_chiffres_et_jamais_des_graines(self):
        """🔴 La propriété du module, éprouvée du côté de l'application.

        Une graine base32 ou une adresse `otpauth://` qui sortirait par cette
        route voudrait dire que le serveur en détient une.
        """
        import base64
        import os
        import re

        self.authenticate("banc-otp-http", "banc-otp-http")
        coffre = self.env["bf.otp.vault"].with_user(self.personne).create({
            "user_id": self.personne.id,
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "iterations": 600000,
            "verifier": base64.b64encode(os.urandom(40)).decode(),
            "verifier_iv": base64.b64encode(os.urandom(12)).decode(),
        })
        self.env["bf.otp.token"].with_user(self.personne).create({
            "vault_id": coffre.id, "issuer": "Banc", "name": "essai@exemple.com",
            "secret_cipher": base64.b64encode(os.urandom(40)).decode(),
            "secret_iv": base64.b64encode(os.urandom(12)).decode(),
        })
        # ⚠️ Aucun `commit()` : Odoo 18 l'interdit dans un test, et
        # HttpCase partage sa transaction avec le serveur HTTP, donc ce qui
        # vient d'être créé est déjà visible par les routes.
        r = self.url_open(
            f"{BASE}/auth/start?redirect=com.bluefoxconsultant.otp://auth",
            allow_redirects=False)
        code = r.headers["Location"].split("code=")[1].split("&")[0]
        jeton = self._json(self.url_open(
            f"{BASE}/auth/exchange", data=json.dumps({"code": code}),
            headers={"Content-Type": "application/json"}))["token"]

        r = self.opener.get(f"{self.base_url()}{BASE}/tokens",
                            headers={"Authorization": f"Bearer {jeton}"})
        corps = r.content.decode()
        self.assertNotIn("otpauth://", corps)
        # Aucune chaîne entièrement base32 d'une longueur de graine réelle.
        for valeur in re.findall(r'"([A-Za-z0-9+/=]{16,})"', corps):
            self.assertFalse(
                re.fullmatch(r"[A-Z2-7]{16,}", valeur),
                f"« {valeur[:12]}… » ressemble à une graine base32 en clair")
        rendus = json.loads(corps)["tokens"]
        self.assertEqual(len(rendus), 1)
        self.assertIn("secret_cipher", rendus[0])
