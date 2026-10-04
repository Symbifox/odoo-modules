from odoo import _, fields, http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.bf_school_homework.controllers.portal import SchoolHomeworkPortal

from ..models.submission import ALLOWED_EXTENSIONS, MAX_FILE_SIZE, MAX_FILES


class SchoolWorkPortal(SchoolHomeworkPortal):

    # --- Who acts for whom ------------------------------------------------------

    @staticmethod
    def _school_work_students():
        """The students this portal user acts for, in sudo.

        🔴 The only door to the work pages. A student's own account acts for that student;
        an adult acts for the children they hold parental authority over. An adult who
        only receives the school's notices sees the homework list, not the hand-ins nor
        the marks.
        """
        partner = request.env.user.partner_id
        students = partner._school_authority_links().student_id
        own = partner.sudo()
        if own.is_student and own.active:
            students |= own
        return students.sudo()

    @staticmethod
    def _school_work_homeworks(student):
        """This student's homework for the current year: the groups they are enrolled in."""
        groups = student.student_enrollment_ids.filtered(
            lambda e: e.state == "active" and e.year_id.state == "current").group_id
        return request.env["bf.school.homework"].sudo().search([
            ("group_id", "in", groups.ids),
            "|", ("submission_mode", "!=", "none"), ("material_ids", "!=", False),
        ], order="date_due desc, id desc")

    def _school_work_pair(self, homework_id, student_id):
        """(homework, student, hand-in or empty), or 404 for anything this user does not act for."""
        students = self._school_work_students()
        student = students.filtered(lambda s: s.id == student_id)
        homework = request.env["bf.school.homework"].sudo().browse(homework_id).exists()
        if not student or not homework or homework not in self._school_work_homeworks(student):
            raise request.not_found()
        submission = request.env["bf.school.homework.submission"].sudo().search(
            [("homework_id", "=", homework.id), ("student_id", "=", student.id)], limit=1)
        return homework, student, submission

    # --- Pages ----------------------------------------------------------------

    @http.route("/my/school/work", type="http", auth="user", website=True)
    def portal_school_work(self, **kw):
        rows = []
        for student in self._school_work_students():
            homeworks = self._school_work_homeworks(student)
            submissions = request.env["bf.school.homework.submission"].sudo().search(
                [("homework_id", "in", homeworks.ids), ("student_id", "=", student.id)])
            by_homework = {s.homework_id: s for s in submissions}
            for homework in homeworks:
                rows.append({"homework": homework, "student": student,
                             "submission": by_homework.get(homework)})
        rows.sort(key=lambda r: (r["homework"].date_due, r["student"].name or ""), reverse=True)
        partner = request.env.user.partner_id
        offer_return, offer_digest = partner._school_work_offers()
        values = self._prepare_portal_layout_values()
        values.update({"page_name": "school_work", "rows": rows,
                       "today": fields.Date.context_today(request.env.user),
                       "offer_return": offer_return, "offer_digest": offer_digest,
                       "me": partner.sudo(), "saved": kw.get("saved")})
        return request.render("bf_school_work.portal_school_work", values)

    @http.route("/my/school/work/notices", type="http", auth="user", methods=["POST"], website=True)
    def portal_school_work_notices(self, **post):
        """The person chooses their own emails, among those their children's school offers."""
        partner = request.env.user.partner_id
        offer_return, offer_digest = partner._school_work_offers()
        vals = {}
        if offer_return:
            vals["school_work_return_email"] = bool(post.get("return_email"))
        if offer_digest:
            vals["school_work_digest"] = bool(post.get("digest"))
            # Ticked again after a while: the next summary covers the last day, not every
            # return since the last one sent (found in review).
            if vals["school_work_digest"] and not partner.sudo().school_work_digest:
                vals["school_work_digest_sent"] = False
        if vals:
            partner.sudo().write(vals)
        return request.redirect("/my/school/work?saved=1")

    @http.route("/my/school/work/<int:homework_id>/<int:student_id>", type="http", auth="user",
                website=True)
    def portal_school_work_page(self, homework_id, student_id, **kw):
        return self._school_work_render(homework_id, student_id)

    def _school_work_render(self, homework_id, student_id, error=None):
        homework, student, submission = self._school_work_pair(homework_id, student_id)
        refusal = ""
        if homework.submission_mode == "file":
            refusal = (submission._school_hand_in_refusal() if submission
                       else self._school_new_refusal(homework, student))
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "school_work_page", "homework": homework, "student": student,
            "submission": submission, "refusal": refusal, "error": error,
            "due_passed": fields.Datetime.now() >= homework._school_due_moment(),
            "max_files": MAX_FILES, "max_file_size_mb": MAX_FILE_SIZE // (1024 * 1024),
            "accept": ",".join("." + e for e in sorted(ALLOWED_EXTENSIONS)),
        })
        return request.render("bf_school_work.portal_school_work_page", values)

    @staticmethod
    def _school_new_refusal(homework, student):
        """The refusal a hand-in would give, before it exists."""
        if (not homework.accept_late
                and fields.Datetime.now() >= homework._school_due_moment()):
            return _("The due date has passed and this work is not accepted late.")
        return ""

    @http.route("/my/school/work/<int:homework_id>/<int:student_id>/hand-in", type="http",
                auth="user", methods=["POST"], website=True,
                max_content_length=MAX_FILES * MAX_FILE_SIZE + 1024 * 1024)
    def portal_school_work_hand_in(self, homework_id, student_id, **post):
        homework, student, submission = self._school_work_pair(homework_id, student_id)
        files = [(f.filename, f.read())
                 for f in request.httprequest.files.getlist("files") if f and f.filename]
        try:
            submission = submission or homework._school_submission_for(student)
            submission._school_hand_in(files, request.env.user.partner_id)
        except UserError as error:
            return self._school_work_render(homework_id, student_id, error=str(error))
        return request.redirect("/my/school/work/%s/%s" % (homework.id, student.id))

    @http.route("/my/school/work/<int:homework_id>/<int:student_id>/quiz", type="http",
                auth="user", methods=["POST"], website=True)
    def portal_school_work_quiz(self, homework_id, student_id, **post):
        homework, student, submission = self._school_work_pair(homework_id, student_id)
        try:
            submission = submission or homework._school_submission_for(student)
            url = submission._school_quiz_url(request.env.user.partner_id)
        except UserError as error:
            return self._school_work_render(homework_id, student_id, error=str(error))
        return request.redirect(url)

    @http.route("/my/school/work/<int:homework_id>/<int:student_id>/file/<int:attachment_id>",
                type="http", auth="user", website=True)
    def portal_school_work_file(self, homework_id, student_id, attachment_id, **kw):
        """A file of this page: the teacher's documents, the work handed in, the corrected copy.

        Served as a download, never inline: a handed-in file comes from a family.
        """
        homework, _student, submission = self._school_work_pair(homework_id, student_id)
        # Each file must also be its record's own (res_model, res_id): what is linked in the
        # fields alone is not proof enough, the stream below is read in sudo.
        allowed = homework.material_ids.filtered(
            lambda a: a.res_model == homework._name and a.res_id == homework.id)
        files = submission.attachment_ids
        if submission.state == "returned":
            files |= submission.correction_ids
        allowed |= files.filtered(
            lambda a: a.res_model == submission._name and a.res_id == submission.id)
        attachment = allowed.filtered(lambda a: a.id == attachment_id and not a.res_field)
        if not attachment:
            raise request.not_found()
        return request.env["ir.binary"]._get_stream_from(attachment).get_response(
            as_attachment=True)

    # --- The homework list links to the work pages --------------------------------

    @http.route()
    def portal_school_homework(self, **kw):
        response = super().portal_school_homework(**kw)
        response.qcontext["school_work_student_ids"] = self._school_work_students().ids
        return response
