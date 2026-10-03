from datetime import date

from odoo.tests import tagged

from .test_school_admission import AdmissionCase


@tagged("post_install", "-at_install")
class TestSubmitRateLimit(AdmissionCase):
    """Every post of the public form counts, even a refused one."""

    def test_refused_attempts_count_too(self):
        # Five posts with a missing field: nothing is created, but each one is counted.
        for __ in range(5):
            response = self._apply(student_firstname="")
            self.assertIn("Please fill in every required field", response.text)
        before = self.env["bf.school.admission"].search_count([])
        # The sixth, valid this time, is refused before anything is read or created.
        response = self._apply()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Too many attempts", response.text)
        self.assertEqual(self.env["bf.school.admission"].search_count([]), before)
        self.assertFalse(self.env["res.partner"].search([("email", "=", "adm.un@example.invalid")]))

    def test_other_address_is_not_blocked(self):
        from ..controllers import portal
        for __ in range(5):
            self.assertTrue(portal._check_submit_rate_limit("203.0.113.9"))
        self.assertFalse(portal._check_submit_rate_limit("203.0.113.9"))
        self.assertTrue(portal._check_submit_rate_limit("203.0.113.10"))
        # The form from 127.0.0.1 still goes through.
        self._apply()
        self.assertIn("adm.un@example.invalid", self._last().guardian_ids.mapped("email"))

    def test_ipv6_rotation_in_a_64_is_one_sender(self):
        # A home or a phone holds a whole /64 and picks a new address in it at will: counted
        # by address, a robot rotating in its /64 was never limited (demo École, 2026-10-02).
        from ..controllers import portal
        for i in range(5):
            self.assertTrue(portal._check_submit_rate_limit("2001:db8:1:2::%x" % (i + 1)))
        self.assertFalse(portal._check_submit_rate_limit("2001:db8:1:2:ffff:ffff:ffff:ffff"))
        self.assertTrue(portal._check_submit_rate_limit("2001:db8:1:3::1"))
        # An IPv4 client seen through IPv6 is its IPv4 address.
        for __ in range(5):
            portal._check_submit_rate_limit("198.51.100.4")
        self.assertFalse(portal._check_submit_rate_limit("::ffff:198.51.100.4"))

    def test_hourly_limit_counts_the_64(self):
        for i in range(3):
            self.env["bf.school.admission"].create({
                "campaign_id": self.campaign.id, "student_firstname": "Rotation%s" % i,
                "student_lastname": "Essai", "student_birthdate": date(2014, 1, 1), "level_id": self.level.id,
                "guardian_ids": [(0, 0, {"name": "Parent %s" % i, "email": "rot%s@example.invalid" % i})],
                "client_ip": "2001:db8:1:2::%x" % (i + 1)})
        self.assertTrue(self.campaign._school_submissions_exceeded("2001:db8:1:2:abcd::9", "neuf@example.invalid"))
        self.assertFalse(self.campaign._school_submissions_exceeded("2001:db8:1:3::1", "neuf@example.invalid"))

    def test_refusal_gives_the_typing_back(self):
        for __ in range(5):
            self._apply(student_firstname="")
        before = self.env["bf.school.admission"].search_count([])
        response = self._apply(student_lastname="Saisie-Gardée", guardian2_name="Deuxième Adulte")
        self.assertEqual(self.env["bf.school.admission"].search_count([]), before, "refused")
        self.assertIn('value="Saisie-Gardée"', response.text)
        self.assertIn('value="Deuxième Adulte"', response.text)

    def test_rotation_across_a_48_is_capped(self):
        # A tunnel or a server holds a whole /48: 65,536 /64, each one a new sender.
        from ..controllers import portal
        for i in range(portal._SUBMIT_MAX_48):
            self.assertTrue(portal._check_submit_rate_limit("2001:db8:7:%x::1" % i))
        self.assertFalse(portal._check_submit_rate_limit("2001:db8:7:ffff::1"))
        self.assertTrue(portal._check_submit_rate_limit("2001:db8:8::1"))

    def test_port_added_by_a_proxy_is_the_same_sender(self):
        from ..controllers import portal
        for port in range(5):
            self.assertTrue(portal._check_submit_rate_limit("198.51.100.7:%s" % (51230 + port)))
        self.assertFalse(portal._check_submit_rate_limit("198.51.100.7"))
        self.assertEqual(portal.school_network("[2001:db8:1:2::9]:443"), "2001:db8:1:2::/64")
        # The hourly limit compares the address stored, without the port.
        for i in range(3):
            self.env["bf.school.admission"].create({
                "campaign_id": self.campaign.id, "student_firstname": "Port%s" % i, "student_lastname": "Essai",
                "student_birthdate": date(2014, 1, 1), "level_id": self.level.id,
                "guardian_ids": [(0, 0, {"name": "Parent port %s" % i, "email": "port%s@example.invalid" % i})],
                "client_ip": portal.school_address("198.51.100.8:%s" % (40000 + i))})
        self.assertTrue(self.campaign._school_submissions_exceeded("198.51.100.8:41000", "neuf@example.invalid"))
        self.assertEqual(portal.school_network("pas une adresse"), "unparsed")
