"""Past 10,000 IPs, the admission limiter used to `clear()` and the blocked
IP started over. Replayed: block an IP, cycle past the cap, retry."""
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_school_admission.controllers import portal


@tagged("post_install", "-at_install")
class TestLimiterBounded(TransactionCase):

    def setUp(self):
        super().setUp()
        portal._submit_data.clear()
        self.addCleanup(portal._submit_data.clear)

    def test_blocked_ip_stays_blocked(self):
        for _i in range(portal._SUBMIT_MAX):
            self.assertTrue(portal._check_submit_rate_limit("203.0.113.7"))
        self.assertFalse(portal._check_submit_rate_limit("203.0.113.7"))
        for i in range(portal._MAX_TRACKED_IPS + 10):
            portal._check_submit_rate_limit("10.%d.%d.1" % (i // 256, i % 256))
        self.assertFalse(portal._check_submit_rate_limit("203.0.113.7"),
                         "the blocked IP started over after the flood")
