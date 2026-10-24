import hashlib

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_date

from .common import CHANNELS, DAY_FIELDS, normalise_name, time_label

PERSON_SYSTEM_DEFAULTS = {"state": "active", "id_checked_on": False, "id_checked_by_id": False,
                          "added_by_id": False, "channel": "office", "revoked_on": False, "revoked_by_id": False}
#: Who the person is and when they may come: what the families were told. Revoke, then add again.
PERSON_TERMS = {"student_id", "name", "date_from", "date_to"}

WALK_SYSTEM_DEFAULTS = {"state": "draft", "signed_by_id": False, "signed_on": False, "channel": False,
                        "text_signed": False, "text_hash": False, "revoked_on": False, "revoked_by_id": False}
#: What the adult signed: frozen once signed.
WALK_TERMS = {"student_id", "from_time", "date_from", "date_to", "paper_signer_id"} | set(DAY_FIELDS)


def _authority_adults(student):
    return student.sudo().student_guardian_link_ids.filtered("has_parental_authority").guardian_id


def _school_check_not_restricted(partner, student):
    """🔴 An adult under a pick-up restriction does not choose, on the portal, who else takes the
    child (a friend, or the child alone). The message says nothing of the restriction."""
    restrictions = student.sudo()._childcare_restrictions(student._childcare_local_today())
    if restrictions._school_matches(partner=partner) or restrictions._school_named_in(partner.name):
        raise UserError(_("Contact the school for this change."))


class ChildcareStudentCompany(models.AbstractModel):
    """The company of a record about a student: that of the school whose child care the student
    attends (see res.partner._childcare_company), set when the record is created.

    🔴 Never an empty company: the core leaves the student card without one, and a rule that let
    an empty company through showed a court order of one company to the child care managers of
    every other one.
    """

    _name = "bf.school.childcare.student_company"
    _description = "Child care record that follows the student's school"

    @api.depends("student_id")
    def _compute_company_id(self):
        for record in self:
            record.company_id = record.student_id._childcare_company() if record.student_id else False

    @api.constrains("student_id", "company_id")
    def _check_company(self):
        for record in self:
            if not record.company_id:
                raise ValidationError(_("%s is enrolled in no school: enrol the student first.",
                                        record.student_id.sudo().name))


class ChildcarePickupPerson(models.Model):
    """Someone outside the family whom the adults with parental authority allow to pick up the child."""

    _name = "bf.school.childcare.pickup.person"
    _inherit = ["bf.school.childcare.guarded", "bf.school.childcare.notify", "bf.school.childcare.student_company",
                "mail.thread", "mail.activity.mixin"]
    _description = "Person allowed to pick up a child"
    _order = "student_id, state, name"
    _school_system_defaults = PERSON_SYSTEM_DEFAULTS

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True,
                                 domain=[("is_student", "=", True)])
    company_id = fields.Many2one("res.company", compute="_compute_company_id", store=True, index=True)
    name = fields.Char("Full name", required=True)
    relationship = fields.Char("Relationship to the child", help="For example: aunt, neighbour, babysitter.")
    phone = fields.Char()
    date_from = fields.Date("From", required=True)
    date_to = fields.Date("Until", help="Empty: until revoked.")
    state = fields.Selection([("active", "Allowed"), ("revoked", "Revoked")], default="active", required=True,
                             readonly=True)
    id_checked_on = fields.Datetime("ID checked on", readonly=True,
                                    help="The first time this person came, the educator checked an ID.")
    id_checked_by_id = fields.Many2one("res.users", "ID checked by", readonly=True)
    added_by_id = fields.Many2one("res.partner", "Added by", readonly=True)
    channel = fields.Selection(CHANNELS, "Received by", default="office", required=True, readonly=True)
    revoked_on = fields.Datetime(readonly=True)
    revoked_by_id = fields.Many2one("res.partner", "Revoked by", readonly=True)

    @api.depends("name", "relationship")
    def _compute_display_name(self):
        for person in self:
            person.display_name = "%s (%s)" % (person.name, person.relationship) if person.relationship \
                else (person.name or "")

    @api.constrains("date_from", "date_to", "name")
    def _check_values(self):
        for person in self:
            if not (person.name or "").strip():
                raise ValidationError(_("Give the person's full name."))
            if person.date_to and person.date_to < person.date_from:
                raise ValidationError(_("A permission ends after it starts."))

    @api.model_create_multi
    def create(self, vals_list):
        self._school_guard_create(vals_list)
        if not self.env.su:
            for vals in vals_list:
                vals["added_by_id"] = self.env.user.partner_id.id
        # 🔴 The adult who adds the person never follows it: the note "activity done" of a
        # restriction match would reach them. The thread exists for the manager's activity only.
        persons = super(ChildcarePickupPerson, self.with_context(
            mail_create_nolog=True, mail_create_nosubscribe=True, mail_notrack=True)).create(vals_list)
        persons = persons.with_env(self.env)
        persons._school_flag_restricted()
        persons._school_notify_authority("bf_school_childcare.mail_template_childcare_person_change",
                                         childcare_change="added")
        return persons

    def _school_restricted(self, day):
        """Is this person named by a pick-up restriction of the child on `day`?"""
        self.ensure_one()
        restrictions = self.student_id.sudo()._childcare_restrictions(day)
        return restrictions._school_matches(name=self.name) or restrictions._school_named_in(self.name)

    def _school_flag_restricted(self):
        """🔴 A family added someone the school must refuse: the manager is told, the family is not
        (the order is not theirs to read). The daily sheet refuses the person anyway.

        The activity lives on the authorized person, which only the manager reads: on the student
        card, every staff member (teachers, accounting, educators) read that a restriction exists.
        """
        for person in self.sudo():
            if not person._school_restricted(person.date_from):
                continue
            school = person.student_id._childcare_school(person.date_from) \
                or person.student_id._childcare_school(person.student_id._childcare_local_today())
            responsible = school._childcare_responsible() if school else False
            if responsible:
                person.activity_schedule(
                    "mail.mail_activity_data_todo", user_id=responsible.id,
                    summary=_("Authorized person to review"),
                    note=_("This name matches a pick-up restriction of the child."))

    def write(self, vals):
        self._school_guard_write(vals)
        if not self.env.su and PERSON_TERMS & set(vals):
            raise UserError(_("Who may pick up a child is not edited: revoke the permission and add it again."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_("A permission is revoked, not deleted: it is the trace of who could come."))
        return super().unlink()

    def _is_valid_on(self, day):
        self.ensure_one()
        return self.state == "active" and self.date_from <= day and (not self.date_to or day <= self.date_to)

    def _validity_label(self):
        self.ensure_one()
        if self.date_to:
            return _("from %(start)s to %(end)s", start=format_date(self.env, self.date_from),
                     end=format_date(self.env, self.date_to))
        return _("from %s, until revoked", format_date(self.env, self.date_from))

    def _school_revoke(self, author):
        for person in self.filtered(lambda p: p.state == "active"):
            person.sudo().write({"state": "revoked", "revoked_on": fields.Datetime.now(),
                                 "revoked_by_id": author.id})
            person._school_notify_authority("bf_school_childcare.mail_template_childcare_person_change",
                                            childcare_change="removed")
        return True

    def action_revoke(self):
        self.check_access("write")
        return self._school_revoke(self.env.user.partner_id)

    # --- The family's side (portal) ---------------------------------------------------------

    @api.model
    def _school_add_by(self, partner, student, name, relationship, phone, date_from, date_to=None):
        if student not in partner._school_authority_links().student_id:
            raise AccessError(_("Only an adult with parental authority adds a person."))
        _school_check_not_restricted(partner, student)
        if date_from < student._childcare_local_today():
            raise UserError(_("A permission starts today or later."))
        with self.env.cr.savepoint():
            return self.sudo().create({
                "student_id": student.id, "name": (name or "").strip()[:128],
                "relationship": (relationship or "").strip()[:64], "phone": (phone or "").strip()[:32],
                "date_from": date_from, "date_to": date_to or False,
                "added_by_id": partner.id, "channel": "portal"})

    def _school_remove_by(self, partner):
        self.ensure_one()
        if self.sudo().student_id not in partner._school_authority_links().student_id:
            raise AccessError(_("Only an adult with parental authority removes a person."))
        return self.sudo()._school_revoke(partner)


class ChildcareRestriction(models.Model):
    """Someone who must not pick up the child (court order, decision of the youth protection).

    Entered by the child care manager or the Administration. The educator never reads this
    record: the daily sheet shows, computed in sudo, the name and a flag, never the detail.
    """

    _name = "bf.school.childcare.restriction"
    _inherit = ["bf.school.childcare.student_company"]
    _description = "Pick-up restriction"
    _order = "student_id, name"

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True,
                                 domain=[("is_student", "=", True)])
    company_id = fields.Many2one("res.company", compute="_compute_company_id", store=True, index=True)
    name = fields.Char("Person's full name", required=True)
    partner_id = fields.Many2one("res.partner", "Contact", domain=[("is_student", "=", False)],
                                 help="When the person is a contact of the school (a parent, for example): the "
                                      "school refuses the pick-up even under another spelling of the name.")
    detail = fields.Text(groups="bf_school_childcare.group_school_childcare_manager",
                         help="The order and what it says. Reserved to the child care manager and the "
                              "Administration.")
    document = fields.Binary(attachment=True, groups="bf_school_childcare.group_school_childcare_manager")
    document_name = fields.Char(groups="bf_school_childcare.group_school_childcare_manager")
    date_from = fields.Date("From", help="Empty: in force now.")
    date_to = fields.Date("Until", help="Empty: until archived.")
    active = fields.Boolean(default=True)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for restriction in self:
            if restriction.date_from and restriction.date_to and restriction.date_to < restriction.date_from:
                raise ValidationError(_("A restriction ends after it starts."))

    def unlink(self):
        if not self.env.su:
            raise UserError(_("A restriction is archived, not deleted."))
        return super().unlink()

    def _school_matches(self, partner=None, name=None):
        """Does this restriction name `partner` or someone called `name`?"""
        for restriction in self:
            if partner and restriction.partner_id and partner == restriction.partner_id:
                return True
            names = {normalise_name(restriction.name), normalise_name(restriction.partner_id.name)} - {""}
            if partner and normalise_name(partner.name) in names:
                return True
            if name and normalise_name(name) in names:
                return True
        return False

    def _school_named_in(self, text):
        """Is the name of a restricted person written in `text` (the educator's note)?"""
        words = " %s " % normalise_name(text)
        return any(" %s " % name in words for restriction in self
                   for name in {normalise_name(restriction.name), normalise_name(restriction.partner_id.name)} - {""})


class ChildcareWalkAlone(models.Model):
    """An adult with parental authority allows the child to leave child care alone on some days,
    from some time. Signed on the portal, or on paper and recorded by the office."""

    _name = "bf.school.childcare.walk_alone"
    _inherit = ["bf.school.childcare.weekdays", "bf.school.childcare.guarded", "bf.school.childcare.notify",
                "bf.school.childcare.student_company"]
    _description = "Permission to leave alone"
    _order = "student_id, date_from desc, id desc"
    _school_system_defaults = WALK_SYSTEM_DEFAULTS

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True,
                                 domain=[("is_student", "=", True)])
    company_id = fields.Many2one("res.company", compute="_compute_company_id", store=True, index=True)
    from_time = fields.Float("From (time)", required=True, help="The child may leave alone from this time.")
    date_from = fields.Date("From", required=True)
    date_to = fields.Date("Until", help="Empty: until revoked.")
    state = fields.Selection([("draft", "To sign"), ("active", "Signed"), ("revoked", "Revoked")],
                             default="draft", required=True, readonly=True)
    signed_by_id = fields.Many2one("res.partner", "Signed by", readonly=True)
    signed_on = fields.Datetime(readonly=True)
    channel = fields.Selection([("portal", "Family portal"), ("paper", "On paper, recorded by the office")],
                               "Signed through", readonly=True)
    text_signed = fields.Text("Text accepted", readonly=True,
                              help="The exact text the adult accepted, in the language it was read in.")
    text_hash = fields.Char("Fingerprint of the text signed", readonly=True,
                            help="SHA-256 of the text accepted: the proof it did not change afterwards.")
    revoked_on = fields.Datetime(readonly=True)
    revoked_by_id = fields.Many2one("res.partner", "Revoked by", readonly=True)
    paper_signer_id = fields.Many2one(
        "res.partner", "Signed on paper by", domain="[('id', 'in', signer_candidate_ids)]",
        help="For a paper permission: the adult with parental authority who signed it.")
    signer_candidate_ids = fields.Many2many("res.partner", compute="_compute_signer_candidate_ids")
    terms = fields.Text("Preview of the text", compute="_compute_terms",
                        help="The text as it reads now, in your language. What was signed is the text accepted.")

    @api.depends("student_id")
    def _compute_signer_candidate_ids(self):
        # In sudo: the child care manager reads the student, not the family links.
        for walk in self:
            walk.signer_candidate_ids = _authority_adults(walk.student_id) if walk.student_id else False

    @api.depends("student_id", "from_time", "date_from", "date_to", "mon", "tue", "wed", "thu", "fri")
    @api.depends_context("lang")
    def _compute_terms(self):
        for walk in self:
            walk.terms = walk._school_terms() if walk.student_id and walk.date_from else ""

    @api.depends("student_id", "from_time")
    def _compute_display_name(self):
        for walk in self:
            walk.display_name = "%s · %s" % (walk.student_id.name or "", time_label(walk.from_time))

    @api.constrains("student_id", "from_time", "date_from", "date_to", "mon", "tue", "wed", "thu", "fri")
    def _check_terms(self):
        for walk in self:
            if not 0 <= walk.from_time < 24:
                raise ValidationError(_("The time is a time of day."))
            if walk.date_to and walk.date_to < walk.date_from:
                raise ValidationError(_("A permission ends after it starts."))
            if not walk._weekday_count():
                raise ValidationError(_("Tick at least one day."))
            walk._school_check_age()

    def _school_minimum_age(self, day, school=None):
        """The minimum age of the school the child attends on `day`, else of today's school.

        🔴 Never 0 because no enrolment covers `day`: a permission starting after the end of the
        school year skipped the check and served, signed, the next year.
        """
        self.ensure_one()
        student = self.student_id.sudo()
        school = school or student._childcare_school(day) or student._childcare_school(student._childcare_local_today())
        return school.childcare_walk_alone_min_age if school else 0

    def _school_old_enough(self, day, school=None):
        self.ensure_one()
        minimum = self._school_minimum_age(day, school)
        birthdate = self.student_id.sudo().student_birthdate
        return not minimum or bool(birthdate and relativedelta(day, birthdate).years >= minimum)

    def _school_check_age(self):
        """The school's minimum age, on the first day of the permission."""
        self.ensure_one()
        student = self.student_id.sudo()
        minimum = self._school_minimum_age(self.date_from)
        if not minimum:
            return
        if not student.student_birthdate:
            raise ValidationError(_("The school needs %s's birth date to allow leaving alone.", student.name))
        if not self._school_old_enough(self.date_from):
            raise ValidationError(_("The school allows leaving alone from %s years old.", minimum))

    @api.model_create_multi
    def create(self, vals_list):
        self._school_guard_create(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._school_guard_write(vals)
        if not self.env.su and WALK_TERMS & set(vals) and any(w.state != "draft" for w in self):
            raise UserError(_("A signed permission is not edited: revoke it and sign a new one."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(w.state != "draft" for w in self):
            raise UserError(_("A signed permission is revoked, not deleted."))
        return super().unlink()

    # --- The terms ----------------------------------------------------------------------------

    def _school_terms(self):
        """What the adult accepts, in the reader's language."""
        self.ensure_one()
        return _("I allow %(student)s to leave child care alone on %(days)s, from %(time)s, %(validity)s. "
                 "Once the child has left, the school is no longer responsible for the way home.",
                 student=self.student_id.sudo().name, days=self._weekday_label(), time=time_label(self.from_time),
                 validity=self._validity_label())

    @staticmethod
    def _school_fingerprint(text):
        """SHA-256 of the text accepted, responsibility clause included."""
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    def _school_sign(self, partner, channel, lang=None):
        """Signed by an adult with parental authority: in force from now. `lang`: the language the
        text was read in (the portal page's); by default the signer's."""
        self.ensure_one()
        if partner not in _authority_adults(self.student_id):
            raise AccessError(_("Only an adult with parental authority signs this permission."))
        if self.state != "draft":
            raise UserError(_("This permission is already signed or revoked."))
        # 🔴 Again here: the office may have changed the student of the draft after its checks.
        self._school_check_age()
        lang = lang or partner.lang or self.company_id.partner_id.lang or "fr_CA"
        text = self.with_context(lang=lang)._school_terms()
        self.sudo().write({"state": "active", "signed_by_id": partner.id, "signed_on": fields.Datetime.now(),
                           "channel": channel, "text_signed": text, "text_hash": self._school_fingerprint(text)})
        self._school_notify_authority("bf_school_childcare.mail_template_childcare_walk_alone_change",
                                      childcare_change="signed")
        return True

    def _school_revoke(self, author):
        for walk in self.filtered(lambda w: w.state == "active"):
            walk.sudo().write({"state": "revoked", "revoked_on": fields.Datetime.now(), "revoked_by_id": author.id})
            walk._school_notify_authority("bf_school_childcare.mail_template_childcare_walk_alone_change",
                                          childcare_change="revoked")
        return True

    def _school_time_label(self):
        self.ensure_one()
        return time_label(self.from_time)

    def _school_summary(self):
        """For the family: "Monday, Thursday, from 15:45, from 2026-11-02, until revoked. Signed by X."."""
        self.ensure_one()
        return _("%(days)s, from %(time)s, %(validity)s. Signed by %(signer)s.", days=self._weekday_label(),
                 time=time_label(self.from_time), validity=self._validity_label(), signer=self.signed_by_id.name)

    def _school_permits(self, day, hour, school=None):
        """May the child leave alone on `day` at `hour` (school's clock, hours as a float)?

        🔴 The age too, on `day`: the school may raise its minimum after the permission was signed.
        """
        self.ensure_one()
        return (self.state == "active" and self.date_from <= day and (not self.date_to or day <= self.date_to)
                and self._weekday_ticked(day) and hour >= self.from_time and self._school_old_enough(day, school))

    def _validity_label(self):
        self.ensure_one()
        if self.date_to:
            return _("from %(start)s to %(end)s", start=format_date(self.env, self.date_from),
                     end=format_date(self.env, self.date_to))
        return _("from %s, until revoked", format_date(self.env, self.date_from))

    # --- Buttons (office) ---------------------------------------------------------------------

    def action_record_paper(self):
        """The office records a permission signed on paper by an adult with parental authority."""
        for walk in self:
            walk.check_access("write")
            if not walk.paper_signer_id:
                raise UserError(_("Choose the adult who signed the paper."))
            walk._school_sign(walk.paper_signer_id, "paper")
        return True

    def action_revoke(self):
        self.check_access("write")
        return self._school_revoke(self.env.user.partner_id)

    # --- The family's side (portal) ---------------------------------------------------------

    @api.model
    def _school_sign_by(self, partner, student, days, from_time, date_from, date_to=None, lang=None):
        if student not in partner._school_authority_links().student_id:
            raise AccessError(_("Only an adult with parental authority signs this permission."))
        _school_check_not_restricted(partner, student)
        if not student._childcare_school(student._childcare_local_today()):
            raise UserError(_("Child care is for the preschool and elementary students of the school."))
        if date_from < student._childcare_local_today():
            raise UserError(_("A permission starts today or later."))
        if not student._childcare_school(date_from):
            raise UserError(_("A permission starts during a school year the child is enrolled in."))
        with self.env.cr.savepoint():
            walk = self.sudo().create({"student_id": student.id, "from_time": from_time, "date_from": date_from,
                                       "date_to": date_to or False, **{d: d in days for d in DAY_FIELDS}})
            walk._school_sign(partner, "portal", lang=lang)
        return walk

    def _school_revoke_by(self, partner):
        self.ensure_one()
        if partner not in _authority_adults(self.sudo().student_id):
            raise AccessError(_("Only an adult with parental authority revokes this permission."))
        return self.sudo()._school_revoke(partner)
