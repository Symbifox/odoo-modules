"""Passé 10 000 adresses, le limiteur des dons faisait `clear()` et
l'IP bloquée repartait à zéro. Rejoué : bloquer une IP, faire défiler plus de
10 000 adresses, réessayer depuis l'IP bloquée."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_fundraising_web.controllers import main as ctrl


@tagged("post_install", "-at_install")
class TestLimiteurBorne(TransactionCase):

    def setUp(self):
        super().setUp()
        ctrl._submit_data.clear()
        self.addCleanup(ctrl._submit_data.clear)

    def test_une_ip_bloquee_le_reste(self):
        with patch.object(ctrl, "_client_ip", return_value="203.0.113.7"):
            for _i in range(ctrl._SUBMIT_MAX):
                self.assertTrue(ctrl._check_submit_rate_limit())
            self.assertFalse(ctrl._check_submit_rate_limit())
        for i in range(ctrl._MAX_TRACKED_IPS + 10):
            with patch.object(ctrl, "_client_ip",
                              return_value="10.%d.%d.1" % (i // 256, i % 256)):
                ctrl._check_submit_rate_limit()
        self.assertLessEqual(len(ctrl._submit_data), ctrl._MAX_TRACKED_IPS + 1)
        with patch.object(ctrl, "_client_ip", return_value="203.0.113.7"):
            self.assertFalse(ctrl._check_submit_rate_limit(),
                             "l'IP bloquée repart à zéro après le défilement")
