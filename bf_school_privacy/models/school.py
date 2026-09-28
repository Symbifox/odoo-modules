from odoo import _, models
from odoo.exceptions import AccessError, UserError


class School(models.Model):
    _inherit = "bf.school"

    def _school_current_students(self):
        self.ensure_one()
        return self.env["bf.school.enrollment"].search([
            ("school_id", "=", self.id), ("state", "=", "active"),
            ("year_id", "=", self.current_year_id.id)]).student_id

    def _school_consent_left_out(self, students):
        """[(student, reason)] the consent module would get wrong.

        🔴 Without a birth date nobody knows who consents, and a minor without an adult holding
        parental authority is asked HIMSELF by privacy_consent (it falls back on the student),
        with a portal account made in their name. A recipient whose address is already the
        login of another account makes the account creation fail, and the whole request with it.
        """
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        left_out = []
        for student in students:
            if not student.student_birthdate:
                left_out.append((student, _("no birth date")))
                continue
            authority = student.student_guardian_link_ids.filtered("has_parental_authority").guardian_id
            if student.is_minor_child and not authority:
                left_out.append((student, _("no adult with parental authority")))
                continue
            recipients = authority if student.is_minor_child else student
            for partner in recipients.filtered("email"):
                taken = Users.search([("login", "=ilike", partner.email), ("partner_id", "!=", partner.id)], limit=1)
                if taken:
                    left_out.append((student, _("%s: this address is already another account's login",
                                                partner.name)))
                    break
                if Users.search_count([("partner_id", "=", partner.id), ("active", "=", False)]):
                    left_out.append((student, _("%s: their account is archived", partner.name)))
                    break
        return left_out

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
        left_out = self._school_consent_left_out(students)
        if left_out:
            self.message_post(body=_("Consents not asked, to fix first: %s",
                                     "; ".join("%s (%s)" % (s.name, why) for s, why in left_out)),
                              message_type="notification", subtype_xmlid="mail.mt_note")
            students -= self.env["res.partner"].union(*[s for s, why in left_out])
            if not students:
                raise UserError(_("No student can be asked yet: see the note on the school."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Ask this year's consents") if not left_out else _(
                "Ask this year's consents (%s left out: see the note on the school)", len(left_out)),
            "res_model": "privacy.consent.request.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_partner_ids": [(6, 0, students.ids)],
                "default_group_id": self.env.ref("bf_school_privacy.consent_group_school_year").id,
                "default_send_email": True,
            },
        }
