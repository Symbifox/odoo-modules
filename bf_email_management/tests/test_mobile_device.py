"""Cycle de vie du jeton d'appareil : émission, échange, révocation, purge."""
import base64
import hashlib
import secrets
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import MobileApiCase


def _pkce():
    """Un couple (vérificateur, défi) comme l'application en fabrique un."""
    verificateur = secrets.token_urlsafe(32)
    defi = base64.urlsafe_b64encode(
        hashlib.sha256(verificateur.encode("utf-8")).digest()
    ).decode().rstrip("=")
    return verificateur, defi


@tagged("post_install", "-at_install")
class TestMobileDevice(MobileApiCase):

    def setUp(self):
        super().setUp()
        self.Device = self.env["bf.email.mobile.device"]

    def test_issue_then_resolve(self):
        self.assertEqual(self.Device._resolve(self.device.device_token),
                         self.device)

    def test_unknown_or_empty_token_resolves_to_nothing(self):
        self.assertFalse(self.Device._resolve("jeton-inexistant"))
        self.assertFalse(self.Device._resolve(""))
        self.assertFalse(self.Device._resolve(None))

    def test_deactivated_device_stops_resolving(self):
        self.device.active = False
        self.assertFalse(self.Device._resolve(self.device.device_token))

    def test_archived_user_revokes_the_token(self):
        """Le geste qu'on pose vraiment à un départ, c'est désactiver le compte.

        Les jetons n'expirent pas : sans ce contrôle, le téléphone d'un employé
        parti garderait une boîte fonctionnelle des mois après son départ.
        """
        self.owner.active = False
        self.assertFalse(self.Device._resolve(self.device.device_token))

    def test_downgrade_to_portal_revokes_the_token(self):
        self.owner.write({
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
        })
        self.assertFalse(self.Device._resolve(self.device.device_token))

    # ------------------------------------------------------------- échange
    def test_pending_code_is_single_use(self):
        verificateur, defi = _pkce()
        code = self.Device._issue_pending(self.owner.id, challenge=defi)
        device = self.Device._exchange(code, verificateur)
        self.assertTrue(device)
        self.assertTrue(device.device_token)
        # Un code rejoué ne doit plus rien donner.
        self.assertFalse(self.Device._exchange(code, verificateur))

    def test_expired_code_is_refused(self):
        verificateur, defi = _pkce()
        code = self.Device._issue_pending(self.owner.id, challenge=defi)
        stale = self.Device.sudo().search([("pending_code", "=", code)])
        stale.pending_code_expiry = fields.Datetime.now() - timedelta(minutes=1)
        self.assertFalse(self.Device._exchange(code, verificateur))

    # ---------------------------------------------------------------- PKCE
    def test_code_alone_is_worth_nothing(self):
        """Le cas qui motive le lot : une autre app a intercepté le code.

        Elle a le code, elle n'a pas le vérificateur — il n'est jamais sorti de
        l'application qui a lancé l'appariement.
        """
        _, defi = _pkce()
        code = self.Device._issue_pending(self.owner.id, challenge=defi)
        self.assertFalse(self.Device._exchange(code))
        self.assertFalse(self.Device._exchange(code, ""))

    def test_wrong_verifier_is_refused(self):
        autre, _ = _pkce()
        _, defi = _pkce()
        code = self.Device._issue_pending(self.owner.id, challenge=defi)
        self.assertFalse(self.Device._exchange(code, autre))

    def test_a_refused_exchange_drops_the_pending_row(self):
        """Sinon le code se rejoue jusqu'à ce que la bonne app se présente,
        et l'application qui l'a intercepté garde sa chance."""
        verificateur, defi = _pkce()
        code = self.Device._issue_pending(self.owner.id, challenge=defi)
        ligne = self.Device.sudo().search([("pending_code", "=", code)])
        self.assertTrue(ligne)

        self.assertFalse(self.Device._exchange(code, "mauvais"))

        self.assertFalse(ligne.exists())
        self.assertFalse(self.Device._exchange(code, verificateur))

    def test_challenge_is_cleared_by_a_good_exchange(self):
        verificateur, defi = _pkce()
        code = self.Device._issue_pending(self.owner.id, challenge=defi)
        device = self.Device._exchange(code, verificateur)
        self.assertFalse(device.sudo().pkce_challenge)

    def test_a_pairing_without_challenge_cannot_be_exchanged(self):
        """Une ligne sans défi ne s'échange contre rien.

        ⚠️ Elle ne peut plus naître par la route — ``/auth/start`` exige le
        défi — mais le modèle ne s'appuie pas là-dessus pour être sûr.
        """
        code = self.Device._issue_pending(self.owner.id)
        self.assertFalse(self.Device._exchange(code, "n-importe-quoi"))

    def test_abandoned_logins_are_swept(self):
        """Un onglet de connexion refermé laisse une ligne portant un jeton."""
        code = self.Device._issue_pending(self.owner.id)
        row = self.Device.sudo().search([("pending_code", "=", code)])
        row.pending_code_expiry = fields.Datetime.now() - timedelta(minutes=10)

        purged = self.Device._gc_pending()

        self.assertEqual(purged, 1)
        self.assertFalse(row.exists())
        # L'appareil réellement utilisé n'est pas emporté au passage.
        self.assertTrue(self.device.exists())

    def test_sweep_spares_a_live_pending_code(self):
        verificateur, defi = _pkce()
        code = self.Device._issue_pending(self.owner.id, challenge=defi)
        self.assertEqual(self.Device._gc_pending(), 0)
        self.assertTrue(self.Device._exchange(code, verificateur))
