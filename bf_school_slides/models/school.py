from odoo import api, models


class SchoolEnrollment(models.Model):
    _inherit = "bf.school.enrollment"

    @api.model_create_multi
    def create(self, vals_list):
        enrollments = super().create(vals_list)
        self.env["slide.channel"]._school_sync_for_groups(enrollments.group_id)
        return enrollments

    def write(self, vals):
        groups = self.group_id
        res = super().write(vals)
        if {"state", "group_id", "student_id"} & set(vals):
            self.env["slide.channel"]._school_sync_for_groups(groups | self.group_id)
        return res

    def unlink(self):
        groups = self.group_id
        res = super().unlink()
        self.env["slide.channel"]._school_sync_for_groups(groups)
        return res


class GuardianLink(models.Model):
    _inherit = "bf.school.guardian.link"

    def _school_course_groups(self):
        return self.sudo().student_id.student_enrollment_ids.group_id

    @api.model_create_multi
    def create(self, vals_list):
        links = super().create(vals_list)
        self.env["slide.channel"]._school_sync_for_groups(links._school_course_groups())
        return links

    def write(self, vals):
        groups = self._school_course_groups()
        res = super().write(vals)
        if {"receives_notices", "student_id", "guardian_id"} & set(vals):
            self.env["slide.channel"]._school_sync_for_groups(groups | self._school_course_groups())
        return res

    def unlink(self):
        groups = self._school_course_groups()
        res = super().unlink()
        self.env["slide.channel"]._school_sync_for_groups(groups)
        return res


class SchoolGroup(models.Model):
    _inherit = "bf.school.group"

    def unlink(self):
        # The course link goes with the group in SQL, without the ORM: the attendees it brought
        # would keep the course (found in review).
        channels = self.env["slide.channel"].sudo().search([("school_group_ids", "in", self.ids)])
        res = super().unlink()
        channels.exists()._school_sync_members()
        return res
