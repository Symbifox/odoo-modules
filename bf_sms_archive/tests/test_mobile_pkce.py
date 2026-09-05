# -*- coding: utf-8 -*-
"""L'appariement mobile exige PKCE.

Ce qui se joue : un schéma d'application personnalisé n'est pas exclusif sur
Android. Une autre application peut déclarer ``com.bluefoxconsultant.sms://auth``
et recevoir le code d'appariement à la place de la nôtre. Le seul contrôle qui
la distingue est le vérificateur PKCE, qui n'est jamais sorti de l'application
qui a lancé l'appariement.

⚠️ L'allowlist de schémas de redirection ne couvre pas ce cas : elle ferme la
redirection ouverte côté serveur, pas l'interception locale du code.
"""
import base64
import hashlib
import secrets
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

BASE = "/bf_sms_archive/mobile/v1"


def _pkce():
    """Un couple (vérificateur, défi) comme l'application en fabrique un."""
    verificateur = secrets.token_urlsafe(32)
    defi = base64.urlsafe_b64encode(
        hashlib.sha256(verificateur.encode("utf-8")).digest()
    ).decode().rstrip("=")
    return verificateur, defi


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestMobilePkce(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="sms_pkce_user",
            groups="bf_sms_archive.group_sms_user",
        )
        cls.Device = cls.env["sms.archive.mobile.device"]
        # ⚠️ Le schéma par défaut est `odoosms://` ; celui de Comms est ajouté
        # par paramètre chez chaque locataire. Le banc le pose lui-même plutôt
        # que de supposer la configuration d'une instance.
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_sms_archive.mobile_redirect_schemes",
            "com.bluefoxconsultant.sms://")

    # ------------------------------------------------------------- modèle
    def test_a_good_verifier_opens_the_exchange(self):
        verificateur, defi = _pkce()
        code = self.Device._issue_pending(self.user.id, challenge=defi)
        device = self.Device._exchange(code, verificateur)
        self.assertTrue(device)
        self.assertTrue(device.device_token)
        # Le défi est consommé avec le code.
        self.assertFalse(device.sudo().pkce_challenge)
        # Un code rejoué ne donne plus rien.
        self.assertFalse(self.Device._exchange(code, verificateur))

    def test_the_code_alone_is_worth_nothing(self):
        """Le cas qui motive le lot : une autre app a intercepté le code."""
        _, defi = _pkce()
        code = self.Device._issue_pending(self.user.id, challenge=defi)
        self.assertFalse(self.Device._exchange(code))
        self.assertFalse(self.Device._exchange(code, ""))

    def test_a_wrong_verifier_is_refused(self):
        autre, _ = _pkce()
        _, defi = _pkce()
        code = self.Device._issue_pending(self.user.id, challenge=defi)
        self.assertFalse(self.Device._exchange(code, autre))

    def test_a_refused_exchange_drops_the_pending_row(self):
        """Sinon le code se rejoue jusqu'à ce que la bonne application se
        présente, et celle qui l'a intercepté garde sa chance."""
        verificateur, defi = _pkce()
        code = self.Device._issue_pending(self.user.id, challenge=defi)
        ligne = self.Device.sudo().search([("pending_code", "=", code)])
        self.assertTrue(ligne)

        self.assertFalse(self.Device._exchange(code, "mauvais"))

        self.assertFalse(ligne.exists())
        self.assertFalse(self.Device._exchange(code, verificateur))

    def test_an_expired_code_drops_its_row_too(self):
        verificateur, defi = _pkce()
        code = self.Device._issue_pending(self.user.id, challenge=defi)
        ligne = self.Device.sudo().search([("pending_code", "=", code)])
        ligne.pending_code_expiry = fields.Datetime.now() - timedelta(minutes=1)

        self.assertFalse(self.Device._exchange(code, verificateur))

        self.assertFalse(ligne.exists())

    def test_a_pairing_without_challenge_cannot_be_exchanged(self):
        """⚠️ Une telle ligne ne peut plus naître par la route — ``/auth/start``
        exige le défi — mais le modèle ne s'appuie pas là-dessus."""
        code = self.Device._issue_pending(self.user.id)
        self.assertFalse(self.Device._exchange(code, "n-importe-quoi"))

    def test_the_password_route_still_issues_a_device(self):
        """⚠️ PKCE ne concerne QUE l'aller-retour par le navigateur.

        ``_issue`` sert la route par mot de passe, qui rend le jeton dans sa
        réponse HTTPS sans qu'un code transite par un lien profond : il n'y a
        rien à intercepter, donc rien à lier.
        """
        device = self.Device._issue(self.user.id, name="Banc")
        self.assertTrue(device.device_token)
        self.assertEqual(self.Device._resolve(device.device_token), device)

    # -------------------------------------------------------------- route
    def test_auth_start_refuses_a_pairing_without_a_challenge(self):
        self.authenticate("sms_pkce_user", "sms_pkce_user")
        reponse = self.url_open(
            BASE + "/auth/start?redirect=com.bluefoxconsultant.sms://auth"
                   "&state=abc",
            timeout=30, allow_redirects=False)
        self.assertEqual(reponse.status_code, 302)
        emplacement = reponse.headers.get("Location", "")
        self.assertIn("error=pkce_required", emplacement)
        self.assertNotIn("code=", emplacement)

    def test_auth_start_refuses_the_plain_method(self):
        """« plain » laisserait passer un défi égal à son vérificateur."""
        _, defi = _pkce()
        self.authenticate("sms_pkce_user", "sms_pkce_user")
        reponse = self.url_open(
            BASE + "/auth/start?redirect=com.bluefoxconsultant.sms://auth"
                   "&state=abc&code_challenge=" + defi
            + "&code_challenge_method=plain",
            timeout=30, allow_redirects=False)
        self.assertIn("error=pkce_required",
                      reponse.headers.get("Location", ""))

    def test_auth_start_hands_a_code_back_with_a_challenge(self):
        _, defi = _pkce()
        self.authenticate("sms_pkce_user", "sms_pkce_user")
        reponse = self.url_open(
            BASE + "/auth/start?redirect=com.bluefoxconsultant.sms://auth"
                   "&state=abc&code_challenge=" + defi
            + "&code_challenge_method=S256",
            timeout=30, allow_redirects=False)
        self.assertEqual(reponse.status_code, 302)
        emplacement = reponse.headers.get("Location", "")
        self.assertTrue(emplacement.startswith("com.bluefoxconsultant.sms://auth"))
        self.assertIn("code=", emplacement)
        self.assertIn("state=abc", emplacement)
