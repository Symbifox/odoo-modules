from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

#: What a subject's components are, by result mode (Education Regulation s. 30.1, second paragraph).
COMPONENT_KINDS = {
    "competency": ("competency",),
    "volet": ("theory", "practice"),
    "discipline": (),
}


SYSTEM = "base.group_system"


class SchoolSubject(models.Model):
    """A subject (matière) of the Québec curriculum, shared by the schools of the database.

    The ones that ship with the module have no company: every school reads them and only the system
    administration changes them, since a change reaches the courses of every school. A school's own
    subjects carry its company.

    The regulation asks for a result by competency in the language of instruction, the second
    language and mathematics, by theory and practice component in the scientific subjects, and
    a single discipline result for every other subject (s. 30.1). The mode says which.
    """

    _name = "bf.school.subject"
    _description = "School subject"
    _order = "stage, sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="Short code, for example FRA or MAT.")
    stage = fields.Selection(
        [("elementary", "Elementary"), ("secondary", "Secondary")], required=True, default="elementary")
    result_mode = fields.Selection(
        [("discipline", "Discipline result only"),
         ("competency", "By competency"),
         ("volet", "By theory and practice component")],
        required=True, default="discipline", string="Result mode")
    units = fields.Integer(
        help="Units of the subject at the secondary level (the regulation, s. 23): earned when "
             "the final result reaches the threshold. Courses can set their own.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", "Company", index=True, ondelete="restrict",
        help="Empty: a subject shared by every company of the database, which only the system administration "
             "changes. A subject a school adds carries its company.")
    component_ids = fields.One2many("bf.school.subject.component", "subject_id", "Components")

    _sql_constraints = [
        ("code_stage_unique", "UNIQUE(code, stage)", "A subject code is used once per stage."),
        ("units_positive", "CHECK(units >= 0)", "Units cannot be negative."),
    ]

    def _is_system(self):
        return self.env.su or self.env.user.has_group(SYSTEM)

    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_system():
            for vals in vals_list:
                # A school's subject carries its company; an empty value is the screen's default, not a request
                # for a shared subject (only the system administration makes one).
                if not vals.get("company_id"):
                    vals["company_id"] = self.env.company.id
                elif vals["company_id"] not in self.env.companies.ids:
                    raise AccessError(_("A subject belongs to one of your companies."))
        return super().create(vals_list)

    def write(self, vals):
        # 🔴 A shared subject feeds the results of every school: the record rule keeps the office to its own,
        # and a school cannot make its subject shared, or another company's, by writing the company.
        if "company_id" in vals and not self._is_system() and vals["company_id"] not in self.env.companies.ids:
            raise AccessError(_("A subject belongs to one of your companies."))
        return super().write(vals)

    @api.constrains("result_mode", "component_ids")
    def _check_components(self):
        for subject in self:
            if subject.result_mode == "discipline" and subject.component_ids:
                raise ValidationError(_(
                    "%s has a single discipline result: it takes no components.", subject.name))


class SchoolSubjectComponent(models.Model):
    """A competency, or a theory or practice component, of a subject."""

    _name = "bf.school.subject.component"
    _description = "Subject component"
    _order = "subject_id, sequence, id"

    subject_id = fields.Many2one("bf.school.subject", required=True, ondelete="cascade", index=True)
    name = fields.Char(required=True, translate=True)
    kind = fields.Selection(
        [("competency", "Competency"), ("theory", "Theory component"),
         ("practice", "Practice component")], required=True, default="competency")
    sequence = fields.Integer(default=10)
    default_weight = fields.Float(
        "Weight (%)", default=0.0,
        help="The weight the evaluation framework gives this component in the discipline "
             "result. Left at 0 until the school enters the framework's figure: a course "
             "does not compute its discipline result on weights nobody has confirmed.")

    _sql_constraints = [
        ("weight_range", "CHECK(default_weight >= 0 AND default_weight <= 100)",
         "A weight is a percentage."),
    ]

    @api.constrains("kind", "subject_id")
    def _check_kind(self):
        for component in self:
            allowed = COMPONENT_KINDS[component.subject_id.result_mode]
            if component.kind not in allowed:
                raise ValidationError(_(
                    "%(component)s cannot be a %(kind)s of %(subject)s.",
                    component=component.name,
                    kind=dict(self._fields["kind"]._description_selection(self.env))[component.kind].lower(),
                    subject=component.subject_id.name))
