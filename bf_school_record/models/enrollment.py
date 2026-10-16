from odoo import api, models


class SchoolEnrollment(models.Model):
    _inherit = "bf.school.enrollment"

    @api.model_create_multi
    def create(self, vals_list):
        enrollments = super().create(vals_list)
        enrollments._school_ensure_record()
        return enrollments

    def _school_ensure_record(self):
        """A student enrolled in a school has a file there: the register cannot miss anyone."""
        Record = self.env["bf.school.record"].sudo()
        for enrollment in self:
            if not Record.search_count([("student_id", "=", enrollment.student_id.id),
                                        ("school_id", "=", enrollment.school_id.id)]):
                Record.create({"student_id": enrollment.student_id.id, "school_id": enrollment.school_id.id})
