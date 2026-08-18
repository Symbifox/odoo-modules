"""Cycle de vie du jeton d'appareil : émission, échange, révocation, purge."""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import MobileApiCase


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
        code = self.Device._issue_pending(self.owner.id)
        device = self.Device._exchange(code)
        self.assertTrue(device)
        self.assertTrue(device.device_token)
        # Un code rejoué ne doit plus rien donner.
        self.assertFalse(self.Device._exchange(code))

    def test_expired_code_is_refused(self):
        code = self.Device._issue_pending(self.owner.id)
        stale = self.Device.sudo().search([("pending_code", "=", code)])
        stale.pending_code_expiry = fields.Datetime.now() - timedelta(minutes=1)
        self.assertFalse(self.Device._exchange(code))

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
        code = self.Device._issue_pending(self.owner.id)
        self.assertEqual(self.Device._gc_pending(), 0)
        self.assertTrue(self.Device._exchange(code))
