"""Passé 10 000 clés, `bf_rate_limit` faisait `clear()` et l'IP
bloquée repartait à zéro. Rejoué : bloquer une IP, faire défiler plus de
10 000 adresses, réessayer depuis l'IP bloquée."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_appointment.controllers import main as ctrl


@tagged("post_install", "-at_install")
class TestLimiteurBorne(TransactionCase):

    def setUp(self):
        super().setUp()
        ctrl._bucket_data.clear()
        self.addCleanup(ctrl._bucket_data.clear)

    def test_reservation_une_ip_bloquee_le_reste(self):
        with patch.object(ctrl, "_client_ip", return_value="203.0.113.7"):
            for _i in range(ctrl._BOOK_MAX):
                self.assertTrue(ctrl._check_book_rate_limit())
            self.assertFalse(ctrl._check_book_rate_limit())
        for i in range(ctrl._MAX_TRACKED_IPS + 10):
            with patch.object(ctrl, "_client_ip",
                              return_value="10.%d.%d.1" % (i // 256, i % 256)):
                ctrl._check_book_rate_limit()
        self.assertLessEqual(len(ctrl._bucket_data), ctrl._MAX_TRACKED_IPS + 1)
        with patch.object(ctrl, "_client_ip", return_value="203.0.113.7"):
            self.assertFalse(ctrl._check_book_rate_limit(),
                             "l'IP bloquée repart à zéro après le défilement")

    def test_jeton_une_ip_bloquee_le_reste(self):
        with patch.object(ctrl, "_client_ip", return_value="203.0.113.9"):
            for _i in range(ctrl._TOKEN_FAIL_MAX):
                ctrl._record_token_failure()
            self.assertFalse(ctrl._check_token_rate_limit())
        for i in range(ctrl._MAX_TRACKED_IPS + 10):
            with patch.object(ctrl, "_client_ip",
                              return_value="10.%d.%d.2" % (i // 256, i % 256)):
                ctrl._record_token_failure()
        with patch.object(ctrl, "_client_ip", return_value="203.0.113.9"):
            self.assertFalse(ctrl._check_token_rate_limit(),
                             "l'IP bloquée repart à zéro après le défilement")
