from odoo import models


class School(models.Model):
    _inherit = "bf.school"

    def action_school_invite_families(self):
        """Invite every adult who receives notices for a student enrolled this year."""
        self.ensure_one()
        students = self.env["bf.school.enrollment"].search([
            ("school_id", "=", self.id), ("state", "=", "active"),
            ("year_id", "=", self.current_year_id.id)]).student_id
        guardians = students.student_guardian_link_ids.filtered("receives_notices").guardian_id
        return self.env["res.partner"]._school_invite(guardians)
