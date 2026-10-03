from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

PARTS = [("full", "Whole day"), ("am", "Morning"), ("pm", "Afternoon")]


class SchoolGroup(models.Model):
    _inherit = "bf.school.group"

    attendance_mode = fields.Selection(
        [("half_day", "Morning and afternoon"), ("period", "Each period")],
        default="half_day", required=True,
        help="Elementary schools usually take attendance twice a day, secondary schools at "
             "every period. No regulation sets the frequency.")
    periods_per_day = fields.Integer("Periods per day", default=5)


class AbsenceReason(models.Model):
    _name = "bf.school.absence.reason"
    _description = "Absence reason"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    is_justified = fields.Boolean(
        "Justified", default=True,
        help="A justified (motivée) absence sends no alert to the family.")
    portal_selectable = fields.Boolean("Families may choose it", default=True)
    active = fields.Boolean(default=True)


class AbsenceDeclaration(models.Model):
    """What the family (or the office, on the phone) says about an absence."""

    _name = "bf.school.absence.declaration"
    _description = "Absence declared by the family"
    _order = "date_from desc, id desc"
    _rec_names_search = ["student_id", "reason_id"]

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True)
    date_from = fields.Date("From", required=True)
    date_to = fields.Date("To", required=True)
    part = fields.Selection(PARTS, default="full", required=True,
                            help="For a single day: the whole day, or only the morning or afternoon.")
    reason_id = fields.Many2one("bf.school.absence.reason", "Reason", required=True, ondelete="restrict")
    comment = fields.Text()
    declared_by_id = fields.Many2one("res.partner", "Declared by", ondelete="set null")
    source = fields.Selection([("portal", "Family portal"), ("office", "School office")],
                              default="office", required=True)
    company_id = fields.Many2one("res.company", related="student_id.company_id", store=True)

    @api.depends("student_id", "date_from", "date_to", "reason_id")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for declaration in self:
            dates = " – ".join(dict.fromkeys(filter(None, [
                fields.Date.to_string(declaration.date_from), fields.Date.to_string(declaration.date_to)])))
            declaration.display_name = " · ".join(filter(None, [
                declaration.student_id.name, dates, declaration.reason_id.name]))

    @api.constrains("date_from", "date_to", "part")
    def _check_dates(self):
        for declaration in self:
            if declaration.date_to < declaration.date_from:
                raise ValidationError(_("An absence ends after it starts."))
            if declaration.part != "full" and declaration.date_to != declaration.date_from:
                raise ValidationError(_("A morning or afternoon absence covers a single day."))

    def _covers(self, day, half):
        """Does this declaration cover this day and half ("am", "pm" or None for a period)?"""
        self.ensure_one()
        if not (self.date_from <= day <= self.date_to):
            return False
        return self.part == "full" or half is None or self.part == half

    @api.model_create_multi
    def create(self, vals_list):
        declarations = super().create(vals_list)
        declarations._apply_to_sessions()
        return declarations

    def _apply_to_sessions(self):
        """A declaration that arrives after attendance was taken justifies the lines it covers."""
        Line = self.env["bf.school.attendance.line"].sudo()
        for declaration in self:
            lines = Line.search([("student_id", "=", declaration.student_id.id),
                                 ("status", "in", ("absent", "late")),
                                 ("session_id.date", ">=", declaration.date_from),
                                 ("session_id.date", "<=", declaration.date_to)])
            for line in lines.filtered(lambda l: declaration._covers(l.session_id.date, l.session_id._half())):
                line.write({"declaration_id": declaration.id, "reason_id": declaration.reason_id.id})


class AttendanceSession(models.Model):
    """One roll call: a group, a day, a half-day or a period."""

    _name = "bf.school.attendance.session"
    _description = "Roll call"
    _order = "date desc, group_id, slot"

    group_id = fields.Many2one("bf.school.group", "Group", required=True, ondelete="cascade", index=True)
    school_id = fields.Many2one(related="group_id.school_id", store=True)
    company_id = fields.Many2one(related="group_id.company_id", store=True)
    date = fields.Date(required=True, default=fields.Date.context_today, index=True)
    slot = fields.Char(
        "Half-day or period", required=True,
        help="am or pm in half-day mode; the period number (1, 2...) in period mode.")
    # What a person reads: « am » was shown raw once the roll call was saved (demo École, 2026-10-02).
    slot_label = fields.Char("Time of day", compute="_compute_slot_label")
    teacher_id = fields.Many2one("res.users", "Taken by", default=lambda s: s.env.user, readonly=True)
    state = fields.Selection([("draft", "In progress"), ("done", "Taken")], default="draft", required=True)
    line_ids = fields.One2many("bf.school.attendance.line", "session_id", "Students")
    absent_count = fields.Integer("Absent", compute="_compute_counts")
    late_count = fields.Integer("Late", compute="_compute_counts")

    _sql_constraints = [
        ("one_roll_call", "UNIQUE(group_id, date, slot)", "Attendance is taken once per group and slot."),
    ]

    @api.depends("line_ids.status")
    def _compute_counts(self):
        for session in self:
            session.absent_count = len(session.line_ids.filtered(lambda l: l.status == "absent"))
            session.late_count = len(session.line_ids.filtered(lambda l: l.status == "late"))

    @api.depends_context("lang")
    def _compute_display_name(self):
        for session in self:
            session.display_name = "%s, %s, %s" % (session.group_id.name, session.date, session._slot_label())

    @api.depends("slot")
    @api.depends_context("lang")
    def _compute_slot_label(self):
        for session in self:
            label = session._slot_label() if session.slot else ""
            session.slot_label = label[:1].upper() + label[1:]

    def _half(self):
        self.ensure_one()
        return self.slot if self.slot in ("am", "pm") else None

    def _slot_label(self):
        self.ensure_one()
        return {"am": _("morning"), "pm": _("afternoon")}.get(self.slot) or _("period %s", self.slot)

    @api.constrains("slot", "group_id")
    def _check_slot(self):
        for session in self:
            if session.group_id.attendance_mode == "half_day" and session.slot not in ("am", "pm"):
                raise ValidationError(_("This group takes attendance in the morning and afternoon: am or pm."))
            if session.group_id.attendance_mode == "period":
                if not session.slot.isdigit() or not 1 <= int(session.slot) <= session.group_id.periods_per_day:
                    raise ValidationError(_("This group takes attendance at each period: 1 to %s.",
                                            session.group_id.periods_per_day))

    @api.model_create_multi
    def create(self, vals_list):
        sessions = super().create(vals_list)
        for session in sessions:
            session._fill_lines()
        return sessions

    def _fill_lines(self):
        """Everyone present by default; what the family declared is already there."""
        self.ensure_one()
        students = self.group_id.sudo()._active_students()
        declarations = self.env["bf.school.absence.declaration"].sudo().search([
            ("student_id", "in", students.ids),
            ("date_from", "<=", self.date), ("date_to", ">=", self.date)])
        values = []
        for student in students:
            declaration = declarations.filtered(
                lambda d: d.student_id == student and d._covers(self.date, self._half()))[:1]
            values.append({"session_id": self.id, "student_id": student.id,
                           "status": "absent" if declaration else "present",
                           "declaration_id": declaration.id, "reason_id": declaration.reason_id.id})
        self.env["bf.school.attendance.line"].sudo().create(values)

    def action_done(self):
        """Attendance is taken: families of unjustified absences are told today."""
        for session in self:
            if session.state == "done":
                continue
            session.state = "done"
            session.line_ids.filtered(
                lambda l: l.status == "absent" and not l.justified)._notify_families()
        return True

    def action_reopen(self):
        self.write({"state": "draft"})
        return True


class AttendanceLine(models.Model):
    _name = "bf.school.attendance.line"
    _description = "Attendance of a student"
    _order = "session_id, student_id"
    _rec_names_search = ["student_id"]

    session_id = fields.Many2one("bf.school.attendance.session", required=True, ondelete="cascade", index=True)
    date = fields.Date(related="session_id.date", store=True, index=True)
    group_id = fields.Many2one(related="session_id.group_id", store=True)
    company_id = fields.Many2one(related="session_id.company_id", store=True)
    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True)
    status = fields.Selection([("present", "Present"), ("absent", "Absent"), ("late", "Late")],
                              default="present", required=True)
    minutes_late = fields.Integer("Minutes late")
    reason_id = fields.Many2one("bf.school.absence.reason", "Reason", ondelete="restrict")
    declaration_id = fields.Many2one("bf.school.absence.declaration", "Declaration", ondelete="set null")
    justified = fields.Boolean(compute="_compute_justified", store=True)
    family_notified_on = fields.Datetime(readonly=True)
    note = fields.Char(help="Internal. Never shown to the family.")

    _sql_constraints = [
        ("one_line", "UNIQUE(session_id, student_id)", "A student appears once per roll call."),
    ]

    @api.depends("student_id", "session_id")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for line in self:
            line.display_name = "%s · %s" % (line.student_id.name or "", line.session_id.display_name or "")

    @api.depends("status", "reason_id.is_justified", "declaration_id")
    def _compute_justified(self):
        for line in self:
            line.justified = line.status == "present" or bool(
                line.declaration_id or line.reason_id.is_justified)

    def _notify_families(self):
        """One email per adult who receives notices, at most once a day per student."""
        template = self.env.ref("bf_school_attendance.mail_template_unjustified_absence",
                                raise_if_not_found=False)
        now = fields.Datetime.now()
        for line in self.sudo():
            already = self.sudo().search_count([
                ("student_id", "=", line.student_id.id), ("date", "=", line.date),
                ("family_notified_on", "!=", False)])
            if already:
                continue
            line.family_notified_on = now
            if not template:
                continue
            adults = line.student_id.student_guardian_link_ids.filtered("receives_notices").guardian_id
            for adult in adults.filtered("email"):
                lang = adult.lang or line.company_id.partner_id.lang or "fr_CA"
                template.with_context(lang=lang, school_lang=lang).send_mail(
                    line.id, force_send=False,
                    email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                    email_values={"recipient_ids": [(6, 0, adult.ids)], "email_to": False})
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()

    @api.model
    def _repeated_unjustified(self, days=30, threshold=3):
        """Students with at least `threshold` unjustified absent days in the last `days` days.

        An aid for the office. In a public school, LIP s. 18 then asks the principal to act
        with the parents and, failing that, to report to the DPJ; in a private school the
        duty to report (Youth Protection Act s. 38-39) weighs on every staff member.
        """
        since = fields.Date.context_today(self) - timedelta(days=days)
        lines = self.sudo().search([("status", "=", "absent"), ("justified", "=", False),
                                    ("date", ">=", since)])
        counts = {}
        for line in lines:
            counts.setdefault(line.student_id, set()).add(line.date)
        return {student: len(dates) for student, dates in counts.items() if len(dates) >= threshold}

    @api.model
    def _action_repeated_unjustified(self):
        if not self.env.user.has_group("bf_school_core.group_school_manager"):
            raise UserError(_("Only the school office sees this list."))
        counts = self._repeated_unjustified()
        return {
            "type": "ir.actions.act_window",
            "name": _("Repeated unjustified absences (30 days, 3 days or more)"),
            "res_model": "res.partner",
            "view_mode": "list,form",
            "domain": [("id", "in", [s.id for s in counts])],
        }

    # --- Portal ------------------------------------------------------------------------

    @api.model
    def _school_portal_children(self, partner):
        return partner._school_portal_links().filtered("receives_notices").student_id
