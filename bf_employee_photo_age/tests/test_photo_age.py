import base64

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged

PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))
PNG2 = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))


@tagged("post_install", "-at_install")
class TestPhotoAge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("bf_employee_photo_age.months", "24")
        cls.user = new_test_user(cls.env, login="photo_emp", name="Photo Person", groups="base.group_user")
        cls.employee = cls.env["hr.employee"].create({"name": "Photo Person", "user_id": cls.user.id})
        cls.activity_type = cls.env.ref("bf_employee_photo_age.mail_activity_type_photo_update")

    def _age(self, months):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE hr_employee SET photo_date = %s WHERE id = %s",
            (fields.Datetime.now() - relativedelta(months=months, days=1), self.employee.id))
        self.employee.invalidate_recordset()

    def _reminders(self):
        return self.env["mail.activity"].search([
            ("res_model", "=", "res.partner"), ("res_id", "=", self.user.partner_id.id),
            ("activity_type_id", "=", self.activity_type.id)])

    def test_a_drawn_avatar_is_not_a_photo(self):
        # hr drew an SVG avatar at creation.
        self.assertTrue(self.employee.image_1920)
        self.assertFalse(self.employee.photo_date)
        self.employee.image_1920 = PNG
        self.assertTrue(self.employee.photo_date)
        self.assertEqual(self.employee.photo_age_months, 0)

    def test_reminder_once_then_closed_by_a_new_photo(self):
        self.employee.image_1920 = PNG
        self._age(25)
        self.assertTrue(self.employee.photo_is_stale)
        self.assertIn(self.employee, self.env["hr.employee"].search([("photo_is_stale", "=", True)]))
        Employee = self.env["hr.employee"]
        self.assertEqual(Employee._cron_photo_reminders(), 1)
        self.assertEqual(len(self._reminders()), 1)
        self.assertEqual(self._reminders().user_id, self.user)
        # The date is HR's: the reminder on the contact must not carry it.
        note = str(self._reminders().note)
        self.assertNotIn(str(self.employee.photo_date.year), note)
        self.assertNotIn("months", note)
        self.assertEqual(Employee._cron_photo_reminders(), 0)  # no second reminder
        self.employee.image_1920 = PNG2
        self.assertFalse(self._reminders())
        self.assertFalse(self.employee.photo_is_stale)

    def test_fresh_photo_and_switch_off(self):
        self.employee.image_1920 = PNG
        self._age(23)
        self.assertFalse(self.employee.photo_is_stale)
        self.assertEqual(self.env["hr.employee"]._cron_photo_reminders(), 0)
        self._age(30)
        self.env["ir.config_parameter"].sudo().set_param("bf_employee_photo_age.months", "0")
        self.employee.invalidate_recordset()
        self.assertFalse(self.employee.photo_is_stale)
        self.assertEqual(self.env["hr.employee"]._cron_photo_reminders(), 0)
        self.assertNotIn(self.employee, self.env["hr.employee"].search([("photo_is_stale", "=", True)]))

    def test_user_picture_change_reaches_the_file_only_with_self_editing(self):
        params = self.env["ir.config_parameter"].sudo()
        # hr's own rule: the file already holds a (drawn) picture, so a picture
        # changed in the preferences stays off it without self-editing...
        self.user.with_user(self.user).image_1920 = PNG
        self.assertFalse(self.employee.photo_date)
        self.employee.image_1920 = PNG2
        self._age(30)
        self.env["hr.employee"]._cron_photo_reminders()
        self.assertIn("person in charge of HR", str(self._reminders().note))
        self._reminders().unlink()
        # ...and reaches it, dated, with self-editing on.
        params.set_param("hr.hr_employee_self_edit", "True")
        self.env["hr.employee"]._cron_photo_reminders()
        self.assertIn("Preferences", str(self._reminders().note))
        self.user.with_user(self.user).image_1920 = PNG
        self.assertEqual(self.employee.image_1920, PNG)
        self.assertEqual(self.employee.photo_age_months, 0)
        self.assertFalse(self._reminders())

    def test_employee_without_user_gets_no_reminder(self):
        loner = self.env["hr.employee"].create({"name": "No Login", "image_1920": PNG})
        self.env.flush_all()
        self.env.cr.execute("UPDATE hr_employee SET photo_date = now() - interval '5 years' WHERE id = %s", (loner.id,))
        loner.invalidate_recordset()
        self.assertTrue(loner.photo_is_stale)
        self.assertEqual(self.env["hr.employee"]._cron_photo_reminders(), 0)
