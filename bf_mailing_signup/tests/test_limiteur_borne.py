"""Passé 10 000 clés, le limiteur faisait `clear()` et l'IP bloquée
repartait à zéro. Rejoué : bloquer une IP, défiler, réessayer."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_mailing_signup.controllers import main as ctl


@tagged("post_install", "-at_install")
class TestLimiteurBorne(TransactionCase):

    def setUp(self):
        super().setUp()
        ctl._bucket_data.clear()
        self.addCleanup(ctl._bucket_data.clear)

    def test_une_ip_bloquee_le_reste(self):
        with patch.object(ctl, "_client_ip", return_value="203.0.113.7"):
            for _i in range(ctl._SIGNUP_MAX):
                self.assertTrue(ctl._rate_ok("signup", ctl._SIGNUP_MAX,
                                             ctl._SIGNUP_WINDOW))
            self.assertFalse(ctl._rate_ok("signup", ctl._SIGNUP_MAX,
                                          ctl._SIGNUP_WINDOW))
        for i in range(ctl._MAX_TRACKED_IPS + 10):
            with patch.object(ctl, "_client_ip",
                              return_value="10.%d.%d.1" % (i // 256, i % 256)):
                ctl._rate_ok("signup", ctl._SIGNUP_MAX, ctl._SIGNUP_WINDOW)
        with patch.object(ctl, "_client_ip", return_value="203.0.113.7"):
            self.assertFalse(ctl._rate_ok("signup", ctl._SIGNUP_MAX,
                                          ctl._SIGNUP_WINDOW),
                             "l'IP bloquée repart à zéro après le défilement")
