from odoo import _, models
from odoo.exceptions import AccessError, UserError


class School(models.Model):
    _inherit = "bf.school"

    def _school_current_students(self):
        self.ensure_one()
        return self.env["bf.school.enrollment"].search([
            ("school_id", "=", self.id), ("state", "=", "active"),
            ("year_id", "=", self.current_year_id.id)]).student_id

    def action_school_request_consents(self):
        """Open the consent request of privacy_consent, filled for this year's students.

        The consent module does the rest: one consent per student and purpose,
        skipping those already pending or granted, sent to the guardians with
        parental authority for a minor and to the student from 14.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_school_core.group_school_manager"):
            raise AccessError(_("Only the school administration asks for consents."))
        students = self._school_current_students()
        if not students:
            raise UserError(_("Nobody is enrolled this year."))
        # 🔴 A minor's flag must be right on the day of the request: the consent
        # module reads it at creation to decide who is asked.
        students._school_sync_minor()
        return {
            "type": "ir.actions.act_window",
            "name": _("Ask this year's consents"),
            "res_model": "privacy.consent.request.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_partner_ids": [(6, 0, students.ids)],
                "default_group_id": self.env.ref("bf_school_privacy.consent_group_school_year").id,
                "default_send_email": True,
            },
        }
