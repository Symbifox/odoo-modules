from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class School(models.Model):
    """A school (établissement). One company may run several."""

    _name = "bf.school"
    _description = "School"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(
        "Ministry code",
        help="The code the Ministry of Education gives the school (code d'organisme).")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda s: s.env.company, ondelete="restrict")
    partner_id = fields.Many2one(
        "res.partner", "Address", help="The school's own contact card: address, phone, email.")
    active = fields.Boolean(default=True)
    year_ids = fields.One2many("bf.school.year", "school_id", "School years")
    group_ids = fields.One2many("bf.school.group", "school_id", "Groups")
    current_year_id = fields.Many2one(
        "bf.school.year", "Current year", compute="_compute_current_year_id")
    student_count = fields.Integer("Students", compute="_compute_student_count")

    def _compute_current_year_id(self):
        Year = self.env["bf.school.year"]
        for school in self:
            school.current_year_id = Year.search(
                [("school_id", "=", school.id), ("state", "=", "current")], limit=1)

    def _compute_student_count(self):
        Enrollment = self.env["bf.school.enrollment"]
        for school in self:
            enrollments = Enrollment.search([
                ("school_id", "=", school.id), ("state", "=", "active"),
                ("year_id", "=", school.current_year_id.id)])
            school.student_count = len(enrollments.student_id)

    #: The tenant's branded layout when bluefox_branding is there, else Odoo's light one.
    _SCHOOL_MAIL_LAYOUTS = ("bluefox_branding.bf_mail_layout", "mail.mail_notification_light")

    @api.model
    def _school_mail_layout(self):
        """🔴 A template sent without a layout leaves bare: no logo, no header, no footer
        (found in QA, 2026-09-27). Every school email goes through this."""
        for xmlid in self._SCHOOL_MAIL_LAYOUTS:
            if self.env.ref(xmlid, raise_if_not_found=False):
                return xmlid
        return False

    @api.model
    def _school_url_lang(self, lang):
        """The website's language prefix for `lang` ("/fr"), or "" without the website."""
        if not lang or "website" not in self.env:
            return ""
        code = self.env["res.lang"]._lang_get(lang)
        return "/%s" % code.url_code if code and code.active else ""

    def action_view_students(self):
        self.ensure_one()
        students = self.env["bf.school.enrollment"].search([
            ("school_id", "=", self.id), ("state", "=", "active"),
            ("year_id", "=", self.current_year_id.id)]).student_id
        return {
            "type": "ir.actions.act_window",
            "name": _("Students"),
            "res_model": "res.partner",
            "view_mode": "list,form",
            "domain": [("id", "in", students.ids)],
            "context": {"default_is_student": True},
        }


class SchoolYear(models.Model):
    """A school year. A school has at most one current year."""

    _name = "bf.school.year"
    _description = "School year"
    _order = "date_start desc"

    name = fields.Char(required=True, help="For example 2026-2027.")
    school_id = fields.Many2one("bf.school", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    date_start = fields.Date("Start", required=True)
    date_end = fields.Date("End", required=True)
    state = fields.Selection(
        [("draft", "Upcoming"), ("current", "Current"), ("closed", "Closed")],
        default="draft", required=True)

    _sql_constraints = [
        ("name_school_unique", "UNIQUE(school_id, name)",
         "A school year name is used only once per school."),
    ]

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for year in self:
            if year.date_end <= year.date_start:
                raise ValidationError(_("A school year ends after it starts."))

    @api.constrains("state", "school_id")
    def _check_one_current(self):
        for year in self.filtered(lambda y: y.state == "current"):
            if self.search_count([("school_id", "=", year.school_id.id),
                                  ("state", "=", "current")]) > 1:
                raise ValidationError(
                    _("%s already has a current school year. Close it first.",
                      year.school_id.name))

    def action_set_current(self):
        for year in self:
            year.school_id.year_ids.filtered(
                lambda y: y.state == "current" and y != year).write({"state": "closed"})
            year.state = "current"
        return True

    def action_close(self):
        self.write({"state": "closed"})
        return True


class SchoolLevel(models.Model):
    """A level: pre-K 4 to Secondary V. Shared by every school of the database."""

    _name = "bf.school.level"
    _description = "School level"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    stage = fields.Selection(
        [("preschool", "Preschool"), ("elementary", "Elementary"), ("secondary", "Secondary")],
        required=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("code_unique", "UNIQUE(code)", "A level code is used only once."),
    ]


class SchoolGroup(models.Model):
    """A group (class) for one school year. A group may span several levels."""

    _name = "bf.school.group"
    _description = "School group"
    _inherit = ["mail.thread"]
    _order = "year_id desc, name"

    name = fields.Char(required=True, tracking=True, help="For example 301 or Les Hiboux.")
    school_id = fields.Many2one("bf.school", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    year_id = fields.Many2one(
        "bf.school.year", "School year", required=True, ondelete="restrict", index=True,
        domain="[('school_id', '=', school_id)]")
    level_ids = fields.Many2many("bf.school.level", string="Levels")
    teacher_ids = fields.Many2many(
        "res.users", string="Teachers", domain=[("share", "=", False)],
        help="The staff members who teach this group. They see its students and families.")
    enrollment_ids = fields.One2many("bf.school.enrollment", "group_id", "Enrolments")
    student_count = fields.Integer("Students", compute="_compute_student_count")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("name_year_unique", "UNIQUE(school_id, year_id, name)",
         "A group name is used only once per school year."),
    ]

    @api.depends("enrollment_ids.state")
    def _compute_student_count(self):
        for group in self:
            group.student_count = len(
                group.enrollment_ids.filtered(lambda e: e.state == "active"))

    @api.constrains("school_id", "year_id")
    def _check_year_school(self):
        for group in self:
            if group.year_id.school_id != group.school_id:
                raise ValidationError(_("The school year belongs to another school."))

    def _active_students(self):
        return self.enrollment_ids.filtered(lambda e: e.state == "active").student_id
