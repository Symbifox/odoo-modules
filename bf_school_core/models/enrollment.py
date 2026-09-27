from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SchoolEnrollment(models.Model):
    """A student's place in a group. Leaving keeps the record: it is the school's history."""

    _name = "bf.school.enrollment"
    _description = "Enrolment"
    _order = "year_id desc, group_id, student_id"
    _rec_name = "student_id"

    student_id = fields.Many2one(
        "res.partner", "Student", required=True, ondelete="restrict", index=True,
        domain=[("is_student", "=", True)])
    group_id = fields.Many2one("bf.school.group", "Group", required=True, ondelete="cascade", index=True)
    school_id = fields.Many2one(related="group_id.school_id", store=True, index=True)
    year_id = fields.Many2one(related="group_id.year_id", store=True, index=True)
    company_id = fields.Many2one(related="group_id.company_id", store=True)
    date_start = fields.Date("Since", default=fields.Date.context_today)
    date_end = fields.Date("Left on")
    state = fields.Selection(
        [("active", "Enrolled"), ("left", "Left")], default="active", required=True)

    _sql_constraints = [
        ("student_group_unique", "UNIQUE(student_id, group_id)",
         "A student is enrolled only once in a group."),
    ]

    @api.constrains("student_id")
    def _check_student(self):
        for enrollment in self:
            if not enrollment.student_id.is_student:
                raise ValidationError(
                    _("%s is not marked as a student.", enrollment.student_id.name))

    def action_leave(self):
        self.write({"state": "left", "date_end": fields.Date.context_today(self)})
        return True
