import re

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

#: The Ministry's permanent code: four letters then eight digits.
PERMANENT_CODE = re.compile(r"^[A-Z]{4}\d{8}$")

#: Law 25: under 14, the holder of parental authority consents for the child
#: (Private Sector Act s. 4.1 and 14; Access Act s. 53.1 and 64.1). Used when
#: the company has no privacy framework.
DEFAULT_CONSENT_AGE = 14


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_student = fields.Boolean("Student", index=True)
    student_birthdate = fields.Date("Birth date")
    student_age = fields.Integer("Age", compute="_compute_student_age")
    student_permanent_code = fields.Char(
        "Permanent code", size=12, copy=False, index=True,
        help="The code the Ministry of Education gives every student: four letters "
             "then eight digits.")
    student_enrollment_ids = fields.One2many(
        "bf.school.enrollment", "student_id", "Enrolments")
    student_group_ids = fields.Many2many(
        "bf.school.group", string="Current groups", compute="_compute_student_group_ids")
    student_guardian_link_ids = fields.One2many(
        "bf.school.guardian.link", "student_id", "Guardians")
    guardian_student_link_ids = fields.One2many(
        "bf.school.guardian.link", "guardian_id", "Children at school")
    guardian_student_count = fields.Integer(
        "Number of children at school", compute="_compute_guardian_student_count")

    _sql_constraints = [
        ("student_permanent_code_unique", "UNIQUE(student_permanent_code)",
         "This permanent code already belongs to another student."),
    ]

    @api.depends("student_birthdate")
    def _compute_student_age(self):
        today = fields.Date.context_today(self)
        for partner in self:
            partner.student_age = (
                relativedelta(today, partner.student_birthdate).years
                if partner.student_birthdate else 0)

    @api.depends("student_enrollment_ids.state", "student_enrollment_ids.year_id.state")
    def _compute_student_group_ids(self):
        for partner in self:
            partner.student_group_ids = partner.student_enrollment_ids.filtered(
                lambda e: e.state == "active" and e.year_id.state == "current").group_id

    @api.depends("guardian_student_link_ids")
    def _compute_guardian_student_count(self):
        for partner in self:
            partner.guardian_student_count = len(partner.guardian_student_link_ids)

    @api.constrains("student_permanent_code")
    def _check_student_permanent_code(self):
        for partner in self.filtered("student_permanent_code"):
            if not PERMANENT_CODE.match(partner.student_permanent_code):
                raise ValidationError(_(
                    "%s is not a permanent code: four letters then eight digits, "
                    "for example ABCD12345678.", partner.student_permanent_code))

    @api.model
    def _school_normalise_vals(self, vals):
        code = vals.get("student_permanent_code")
        if code:
            vals["student_permanent_code"] = re.sub(r"\s+", "", code).upper()
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create([self._school_normalise_vals(v) for v in vals_list])
        partners.filtered("is_student")._school_sync_minor()
        return partners

    def write(self, vals):
        result = super().write(self._school_normalise_vals(vals))
        if {"student_birthdate", "is_student"} & set(vals):
            self.filtered("is_student")._school_sync_minor()
        return result

    # --- Minors -----------------------------------------------------------

    def _school_consent_age(self):
        """The age under which a guardian consents, from the company's framework.

        🔴 Read in sudo: `privacy.framework` is readable by the privacy managers only,
        and the school office that types a birth date is not one of them. Without
        sudo, entering a student's birth date was refused to the office.
        """
        self.ensure_one()
        company = (self.company_id or self.env.company).sudo()
        framework = ("default_privacy_framework_id" in company._fields
                     and company.default_privacy_framework_id)
        return (framework and framework.age_of_majority) or DEFAULT_CONSENT_AGE

    def _school_sync_minor(self):
        """Tick or untick `is_minor_child` from the birth date.

        🔴 `is_minor_child` is a checkbox in privacy_consent, ticked by hand. A
        student who turns 14 must stop being routed to the guardians on that day,
        without anyone thinking of it: the daily cron calls this method.
        Without a birth date nothing is changed: the school has not said.
        """
        today = fields.Date.context_today(self)
        for partner in self.filtered("student_birthdate"):
            minor = relativedelta(today, partner.student_birthdate).years < partner._school_consent_age()
            if partner.is_minor_child != minor:
                super(ResPartner, partner.sudo()).write({"is_minor_child": minor})

    @api.model
    def _cron_school_sync_minors(self):
        self.search([("is_student", "=", True), ("student_birthdate", "!=", False),
                     ("is_minor_child", "=", True)])._school_sync_minor()

    # --- Guardians --------------------------------------------------------

    def _school_sync_legal_guardians(self):
        """Mirror the links with parental authority into `legal_guardian_ids`."""
        for student in self.filtered("is_student"):
            guardians = student.sudo().student_guardian_link_ids.filtered(
                "has_parental_authority").guardian_id
            if guardians != student.sudo().legal_guardian_ids:
                super(ResPartner, student.sudo()).write(
                    {"legal_guardian_ids": [(6, 0, guardians.ids)]})

    def _school_children_for_guardian(self, role=None):
        """The students this adult is linked to, optionally through one role field."""
        self.ensure_one()
        links = self.sudo().guardian_student_link_ids
        if role:
            links = links.filtered(role)
        return links.student_id
