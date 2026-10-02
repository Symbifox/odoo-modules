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
