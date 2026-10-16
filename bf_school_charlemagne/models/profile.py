import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .config import MANAGER

#: Cycle and year in the cycle, by level code (Education Regulation s. 15: three cycles of two years
#: at the elementary level; at the secondary level the first cycle is two years, the second three).
CYCLES = {"P1": (1, 1), "P2": (1, 2), "P3": (2, 1), "P4": (2, 2), "P5": (3, 1), "P6": (3, 2),
          "S1": (1, 1), "S2": (1, 2), "S3": (2, 1), "S4": (2, 2), "S5": (2, 3)}


class CharlemagneProfile(models.Model):
    """What Charlemagne asks about a student that the school's own records do not hold.

    The codes (languages, places of birth, programs, teaching order) come from the Ministry's domain
    tables, which the teletransmission guide does not reproduce: the school enters them once and
    they follow the student from year to year. The names are those of the Ministry's register
    (Ariane): the declaration is refused when they do not match, so someone confirms them.
    """

    _name = "bf.school.charlemagne.profile"
    _description = "Student's data for the Ministry"
    _order = "year_id desc, last_name, first_name"
    _rec_name = "student_id"

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict", index=True,
                                 domain=[("is_student", "=", True)])
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="restrict", index=True)
    school_id = fields.Many2one(related="year_id.school_id", store=True, index=True)
    company_id = fields.Many2one(related="year_id.company_id", store=True)
    last_name = fields.Char("Family name", size=40)
    first_name = fields.Char("First name", size=25)
    names_confirmed = fields.Boolean(
        "Names confirmed", help="The family name and the first name entered here are those of the Ministry's register of students "
                                "(Ariane), checked against the permanent code. Charlemagne refuses an identity that does not match.")
    postal_code = fields.Char("Postal code of the residence", size=6)
    mother_tongue_code = fields.Char("Mother tongue (code)", size=3)
    home_language_code = fields.Char("Language spoken at home (code)", size=3)
    birthplace_mother_code = fields.Char("Mother's birthplace (code)", size=3)
    birthplace_father_code = fields.Char("Father's birthplace (code)", size=3)
    free_schooling = fields.Selection(
        [("O", "Yes"), ("N", "No")], "Free schooling by residence status",
        help="IND_GRAT_SCOLR (gratuité scolaire): whether the student's residence status in Québec gives them free schooling.")
    pathway_code = fields.Char("Pathway (code)", size=2)
    declaration_type = fields.Selection(
        [("FIN", "Financing"), ("FRE", "Attendance")], "Kind of declaration", default="FIN", required=True)
    teaching_order = fields.Char(
        "Teaching order (code)", size=1, help="ORDRE_ENSEI. Left empty, the order of the school's settings for the level is used.")
    class_code = fields.Char("Class (échelon)", size=1)
    hours = fields.Integer("Planned hours of attendance")
    program_code = fields.Char("Program (code)", size=10)
    file_number = fields.Char("School file number", size=10, help="The student's number in the school (NO_DOSR_ORGAN_SCOLR). Left empty, an internal number is used.")
    start_date = fields.Date("Attendance start date", help="Left empty, the date the student joined the group.")
    boarding = fields.Selection([("O", "Yes"), ("N", "No")], "Boarder", default="N", required=True)
    end_date = fields.Date("Attendance end date")
    end_reason = fields.Char("Reason for the end of attendance (code)", size=2)
    withdraw = fields.Boolean(
        "Cancel the declaration", help="Ask the Ministry to cancel this year's declaration (SUP). The Ministry keeps it with a cancellation date.")
    group_id = fields.Many2one("bf.school.group", compute="_compute_group")
    ledger_state = fields.Selection(
        [("new", "Not declared"), ("declared", "Declared"), ("changed", "Changed since declared"), ("withdrawn", "Cancelled")],
        compute="_compute_ledger_state")

    _sql_constraints = [
        ("student_year_unique", "UNIQUE(student_id, year_id)", "A student has one profile per school year."),
    ]

    @api.depends("student_id", "year_id")
    def _compute_group(self):
        for profile in self:
            enrollment = self.env["bf.school.enrollment"].search([
                ("student_id", "=", profile.student_id.id), ("year_id", "=", profile.year_id.id)],
                order="state, id desc", limit=1)
            profile.group_id = enrollment.group_id

    @api.depends("student_id", "year_id", "last_name", "first_name", "names_confirmed", "postal_code",
                 "mother_tongue_code", "home_language_code", "birthplace_mother_code", "birthplace_father_code",
                 "free_schooling", "pathway_code", "declaration_type", "teaching_order", "class_code", "hours",
                 "program_code", "file_number", "start_date", "boarding", "end_date", "end_reason", "withdraw")
    def _compute_ledger_state(self):
        Ledger = self.env["bf.school.charlemagne.ledger"].sudo()
        for profile in self:
            entry = Ledger.search([("school_id", "=", profile.school_id.id), ("key", "=", profile._dj_key()),
                                   ("mode", "=", profile._config().mode or "simulation")], limit=1)
            if not entry:
                profile.ledger_state = "new"
            elif entry.state == "withdrawn":
                profile.ledger_state = "withdrawn"
            else:
                profile.ledger_state = "declared"
                try:
                    values = profile._dj_values()
                except ValidationError:
                    values = None
                if values is not None and Ledger._digest(values) != entry.digest:
                    profile.ledger_state = "changed"

    def _dj_key(self):
        self.ensure_one()
        return "DJ|%s|%s" % (self.student_id.id, self.year_id.id)

    @api.model_create_multi
    def create(self, vals_list):
        self._check_office()
        for vals in vals_list:
            student = self.env["res.partner"].browse(vals.get("student_id"))
            if student and not vals.get("last_name") and not vals.get("first_name"):
                vals["first_name"], vals["last_name"] = self._split_name(student.name)
            if student and not vals.get("postal_code"):
                vals["postal_code"] = self._postal_code_of(student)
        return super().create(vals_list)

    def write(self, vals):
        self._check_office()
        if {"last_name", "first_name"} & set(vals) and "names_confirmed" not in vals:
            vals = dict(vals, names_confirmed=False)
        return super().write(vals)

    @api.model
    def _check_office(self):
        if not self.env.su and not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration enters the data for the Ministry."))

    @staticmethod
    def _split_name(name):
        """First name and family name from a contact's full name: a guess, to be confirmed."""
        parts = (name or "").split()
        if len(parts) <= 1:
            return "", name or ""
        return " ".join(parts[:-1]), parts[-1]

    @api.model
    def _postal_code_of(self, student):
        student = student.sudo()
        source = student if student.zip else student.student_guardian_link_ids.filtered(
            lambda l: l.has_parental_authority).sorted(lambda l: not l.lives_with)[:1].guardian_id
        return re.sub(r"\s+", "", source.zip or "").upper()

    @api.model
    def _ensure_profiles(self, year):
        """A profile for every student enrolled this year, from last year's when there is one."""
        year = year.sudo()
        students = self.env["bf.school.enrollment"].sudo().search(
            [("year_id", "=", year.id), ("state", "=", "active")]).student_id
        have = self.sudo().search([("year_id", "=", year.id)]).student_id
        previous = self.sudo().search([("student_id", "in", (students - have).ids), ("year_id", "!=", year.id)],
                                      order="year_id desc")
        created = self.sudo().browse()
        for student in students - have:
            old = previous.filtered(lambda p, s=student: p.student_id == s)[:1]
            vals = {"student_id": student.id, "year_id": year.id}
            if old:
                for field in ("last_name", "first_name", "names_confirmed", "postal_code", "mother_tongue_code",
                              "home_language_code", "birthplace_mother_code", "birthplace_father_code",
                              "free_schooling", "pathway_code", "teaching_order", "program_code", "file_number",
                              "boarding"):
                    value = old[field]
                    if value:
                        vals[field] = value
            created |= self.sudo().create(vals)
        return created

    # --- The declaration, in the guide's elements --------------------------------------------------

    def _enrollment(self):
        self.ensure_one()
        return self.env["bf.school.enrollment"].sudo().search([
            ("student_id", "=", self.student_id.id), ("year_id", "=", self.year_id.id)],
            order="state, id desc", limit=1)

    def _level(self):
        enrollment = self._enrollment()
        if "level_id" in enrollment._fields and enrollment.level_id:
            return enrollment.level_id
        levels = enrollment.group_id.level_ids
        return levels if len(levels) == 1 else self.env["bf.school.level"]

    def _config(self):
        return self.env["bf.school.charlemagne.config"].sudo().search([("school_id", "=", self.school_id.id)], limit=1)

    def _dj_values(self):
        """The elements of the declaration of this student, by tag; raises ValidationError when something is not known."""
        self.ensure_one()
        config, enrollment, level = self._config(), self._enrollment(), self._level()
        student, year = self.student_id.sudo(), self.year_id
        if not enrollment:
            raise ValidationError(_("%s is not enrolled in a group this year.", student.name))
        if not self.names_confirmed:
            raise ValidationError(_("Confirm the names of %s against the Ministry's register first.", student.name))
        record = self.env["bf.school.record"].sudo().search(
            [("student_id", "=", student.id), ("school_id", "=", self.school_id.id)], limit=1)
        language = {"fr": config.teaching_language_fr, "en": config.teaching_language_en}.get(record.teaching_language)
        order = self.teaching_order or {
            "elementary": config.order_code_elementary, "secondary": config.order_code_secondary}.get(level.stage)
        cycle, year_in_cycle = CYCLES.get(level.code, (None, None))
        group = enrollment.group_id
        start = self.start_date or enrollment.date_start
        if self.declaration_type == "FRE" and not start:
            # The guide (6.1): DT_DEBUT_FREQN is optional for a financing declaration and required for an attendance one.
            raise ValidationError(_("%s: an attendance declaration needs the date attendance started.", student.name))
        # A student who left the group has an end of attendance: the school's, or the enrolment's.
        end = self.end_date or (enrollment.date_end if enrollment.state == "left" else None)
        values = {
            "CD_PERMN": student.student_permanent_code, "NOM": self.last_name, "PRENM": self.first_name,
            "AN_SCOLR_PPS": "%s%s" % (year.date_start.year, year.date_end.year),
            "CD_ORGNS": self.school_id.code, "TYPE_PARCR": self.pathway_code,
            "IND_GRAT_SCOLR": self.free_schooling, "CD_POSTL_RESDN": self.postal_code,
            "CD_LANG_MATRN_DETL": self.mother_tongue_code, "CD_LANG_USAGE_DETL": self.home_language_code,
            "LANG_ENSEI": language, "CD_IMMBLE": config.building_code, "DT_FIN_FREQN": end,
            "MOTIF_FIN_FREQN": self.end_reason if end else None,
            "NB_HEURE_FREQN": self.hours or None, "CD_PROGR": self.program_code,
            "NO_DOSR_ORGAN_SCOLR": self.file_number or str(student.id),
            "CD_LIEU_NAISN_MERE": self.birthplace_mother_code, "CD_LIEU_NAISN_PERE": self.birthplace_father_code,
            "TYPE_DECLR": self.declaration_type, "ORDRE_ENSEI": order, "DT_DEBUT_FREQN": start,
            "CLAS": self.class_code, "CYCLE_ENSEI": cycle, "AN_ENSEI": year_in_cycle,
            "CHFFREJS_DECLR_JEUNE_SPECF": {
                "IND_ELEVE_PENSN": self.boarding, "GROUP_ELEVE": group.ministry_group_number or None},
        }
        return self._clean(values)

    @staticmethod
    def _clean(values):
        """Strings only, nothing empty: an absent element is the guide's way to say "no value"."""
        out = {}
        for key, value in values.items():
            if isinstance(value, dict):
                inner = CharlemagneProfile._clean(value)
                if inner:
                    out[key] = inner
            elif value not in (None, False, ""):
                out[key] = str(value)
        return out
