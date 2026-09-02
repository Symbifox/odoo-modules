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

    def test_un_token_deja_utilise_se_rend_quand_meme(self):
        """🔴 Le défaut qui a atteint un vrai téléphone, figé ici.

        `read()` rend `last_used` en `datetime`, que `json` ne sait pas écrire.
        Un coffre neuf n'a aucun token utilisé, donc la valeur y vaut `False` et
        se sérialise très bien : la suite passait au vert et la route tombait
        dès qu'on avait touché un seul code. ⚠️ Un compte d'essai vide n'est pas
        un petit compte, c'est un compte qui n'a pas les valeurs qui cassent.
        """
        import base64
        import os

        from odoo import fields

        self.authenticate("banc-otp-http", "banc-otp-http")
        coffre = self.env["bf.otp.vault"].with_user(self.personne).create({
            "user_id": self.personne.id,
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "iterations": 600000,
            "verifier": base64.b64encode(os.urandom(40)).decode(),
            "verifier_iv": base64.b64encode(os.urandom(12)).decode(),
        })
        client = self.env["res.partner"].create({"name": "Client de banc"})
        self.env["bf.otp.token"].with_user(self.personne).create({
            "vault_id": coffre.id, "issuer": "Banc", "name": "utilise@exemple.com",
            "secret_cipher": base64.b64encode(os.urandom(40)).decode(),
            "secret_iv": base64.b64encode(os.urandom(12)).decode(),
            # Les deux formes que `read()` rend et que `json` ignore : une date,
            # et un Many2one, qui sort en couple.
            "last_used": fields.Datetime.now(),
            "partner_id": client.id,
        })

        r = self.url_open(
            f"{BASE}/auth/start?redirect=com.bluefoxconsultant.otp://auth",
            allow_redirects=False)
        code = r.headers["Location"].split("code=")[1].split("&")[0]
        jeton = self._json(self.url_open(
            f"{BASE}/auth/exchange", data=json.dumps({"code": code}),
            headers={"Content-Type": "application/json"}))["token"]

        r = self.opener.get(f"{self.base_url()}{BASE}/tokens",
                            headers={"Authorization": f"Bearer {jeton}"})
        self.assertEqual(200, r.status_code,
                         "un token déjà utilisé ne doit pas faire échouer la route")
        rendus = self._json(r)["tokens"]
        self.assertEqual(1, len(rendus))
        # La date sort en texte, lisible par l'application.
        self.assertIsInstance(rendus[0]["last_used"], str)
        self.assertTrue(rendus[0]["last_used"])

    def _apparier(self):
        """Rend un jeton porteur pour le compte de banc."""
        self.authenticate("banc-otp-http", "banc-otp-http")
        r = self.url_open(
            f"{BASE}/auth/start?redirect=com.bluefoxconsultant.otp://auth",
            allow_redirects=False)
        code = r.headers["Location"].split("code=")[1].split("&")[0]
        return self._json(self.url_open(
            f"{BASE}/auth/exchange", data=json.dumps({"code": code}),
            headers={"Content-Type": "application/json"}))["token"]

    def _coffre_avec_token(self, **valeurs):
        import base64
        import os
        coffre = self.env["bf.otp.vault"].with_user(self.personne).create({
            "user_id": self.personne.id,
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "iterations": 600000,
            "verifier": base64.b64encode(os.urandom(40)).decode(),
            "verifier_iv": base64.b64encode(os.urandom(12)).decode(),
        })
        base = {
            "vault_id": coffre.id, "issuer": "Banc", "name": "essai@exemple.com",
            "secret_cipher": base64.b64encode(os.urandom(40)).decode(),
            "secret_iv": base64.b64encode(os.urandom(12)).decode(),
        }
        base.update(valeurs)
        return self.env["bf.otp.token"].with_user(self.personne).create(base)

    def test_la_sonde_annonce_le_rp_id(self):
        # 🔴 « bluefoxconsultant.com » et « www.bluefoxconsultant.com » sont
        # deux parties de confiance DIFFÉRENTES pour WebAuthn. Une clé enrôlée
        # sous l'une n'ouvre rien sous l'autre, et l'application n'a aucun moyen
        # de deviner laquelle le site a utilisée : le serveur doit le dire.
        corps = self._json(self.url_open(f"{BASE}/ping"))
        self.assertIn("rp_id", corps)
        self.assertTrue(corps["rp_id"])
        self.assertNotIn(":", corps["rp_id"], "le port n'a rien à faire dans un RP ID")

    def test_une_cle_d_acces_enrolee_du_telephone_est_conservee(self):
        self._coffre_avec_token()
        jeton = self._apparier()
        entetes = {"Authorization": f"Bearer {jeton}",
                   "Content-Type": "application/json"}
        r = self.opener.post(
            f"{self.base_url()}{BASE}/credential/add",
            data=json.dumps({
                "name": "Pixel de banc",
                "credential_id": "Y3JlZC1kZS1iYW5j",
                "prf_salt": "c2VsLWRlLWJhbmM=",
                "wrapped_secret": "c2NlbGxlLWRlLWJhbmM=",
                "wrapped_iv": "dmVjdGV1cg==",
            }), headers=entetes)
        self.assertEqual(200, r.status_code)
        coffre = self._json(r)["vault"]
        self.assertEqual(1, len(coffre["credentials"]))
        self.assertEqual("Pixel de banc", coffre["credentials"][0]["name"])

        # Et elle se retire.
        r = self.opener.post(
            f"{self.base_url()}{BASE}/credential/remove",
            data=json.dumps({"id": coffre["credentials"][0]["id"]}), headers=entetes)
        self.assertEqual(200, r.status_code)
        self.assertEqual(0, len(self._json(r)["vault"]["credentials"]))

    def test_les_routes_de_cles_d_acces_refusent_sans_jeton(self):
        for chemin in ("/credential/add", "/credential/remove"):
            r = self.url_open(f"{BASE}{chemin}", data=json.dumps({}))
            self.assertEqual(401, r.status_code, chemin)

    def test_copier_depuis_le_telephone_horodate_le_token(self):
        # ⚠️ Sans cette route, le tri « les plus récents » ne bougerait que
        # depuis le site, et l'ordre paraîtrait figé sur le téléphone.
        token = self._coffre_avec_token()
        self.assertFalse(token.last_used)
        jeton = self._apparier()
        r = self.opener.post(
            f"{self.base_url()}{BASE}/touch",
            data=json.dumps({"token_id": token.id}),
            headers={"Authorization": f"Bearer {jeton}",
                     "Content-Type": "application/json"})
        self.assertEqual(200, r.status_code)
        token.invalidate_recordset()
        self.assertTrue(token.last_used, "la date d'usage n'a pas été posée")

    def test_le_compteur_hotp_avance_depuis_le_telephone(self):
        # 🔴 Un HOTP est à usage unique. Si le compteur n'avance pas là où le
        # code est produit, le service refuse le deuxième usage.
        token = self._coffre_avec_token(otp_type="hotp", counter=4)
        jeton = self._apparier()
        r = self.opener.post(
            f"{self.base_url()}{BASE}/bump",
            data=json.dumps({"token_id": token.id, "counter": 5}),
            headers={"Authorization": f"Bearer {jeton}",
                     "Content-Type": "application/json"})
        self.assertEqual(200, r.status_code)
        token.invalidate_recordset()
        self.assertEqual(5, token.counter)

    def test_les_routes_d_usage_refusent_sans_jeton(self):
        for chemin in ("/touch", "/bump"):
            r = self.url_open(f"{BASE}{chemin}", data=json.dumps({"token_id": 1}))
            self.assertEqual(401, r.status_code, chemin)

    def test_on_ne_peut_pas_horodater_le_token_de_quelqu_un_d_autre(self):
        # 🔴 La route fait confiance à l'identifiant reçu : c'est la règle
        # d'enregistrement du modèle qui doit empêcher d'atteindre autrui.
        autre = self.env["res.users"].create({
            "name": "Autre", "login": "autre-otp-http",
            "groups_id": [(4, self.env.ref("bf_otp.group_otp_user").id)],
        })
        import base64
        import os
        coffre = self.env["bf.otp.vault"].with_user(autre).create({
            "user_id": autre.id,
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "iterations": 600000,
            "verifier": base64.b64encode(os.urandom(40)).decode(),
            "verifier_iv": base64.b64encode(os.urandom(12)).decode(),
        })
        sien = self.env["bf.otp.token"].with_user(autre).create({
            "vault_id": coffre.id, "issuer": "Autre", "name": "pas@moi.com",
            "secret_cipher": base64.b64encode(os.urandom(40)).decode(),
            "secret_iv": base64.b64encode(os.urandom(12)).decode(),
        })
        jeton = self._apparier()
        r = self.opener.post(
            f"{self.base_url()}{BASE}/touch",
            data=json.dumps({"token_id": sien.id}),
            headers={"Authorization": f"Bearer {jeton}",
                     "Content-Type": "application/json"})
        self.assertNotEqual(200, r.status_code,
                            "le token d'autrui ne doit pas être atteignable")
        sien.invalidate_recordset()
        self.assertFalse(sien.last_used)

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
