from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_date

from .common import CHANNELS

ABSENCE_SYSTEM_DEFAULTS = {"declared_by_id": False, "channel": "office"}


class ChildcareAbsence(models.Model):
    """A family says the child will not come to child care. The place stays due."""

    _name = "bf.school.childcare.absence"
    _inherit = ["bf.school.childcare.guarded"]
    _description = "Child care absence notice"
    _order = "date desc, id desc"
    _school_system_defaults = ABSENCE_SYSTEM_DEFAULTS

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True,
                                 domain=[("is_student", "=", True)])
    school_id = fields.Many2one("bf.school", "School", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True, index=True)
    date = fields.Date(required=True, index=True)
    slot_id = fields.Many2one("bf.school.childcare.slot", "Period", ondelete="cascade",
                              domain="[('school_id', '=', school_id)]", help="Empty: the whole day.")
    reason = fields.Char()
    declared_by_id = fields.Many2one("res.partner", "Declared by", readonly=True)
    channel = fields.Selection(CHANNELS, "Received by", default="office", required=True, readonly=True)

    @api.depends("student_id", "date", "slot_id")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for absence in self:
            absence.display_name = " · ".join(filter(None, [
                absence.student_id.name, format_date(self.env, absence.date) if absence.date else "",
                absence.slot_id.name or _("Whole day")]))

    @api.model_create_multi
    def create(self, vals_list):
        self._school_guard_create(vals_list)
        if not self.env.su:
            for vals in vals_list:
                vals["declared_by_id"] = self.env.user.partner_id.id
        absences = super().create(vals_list)
        for absence in absences:
            if absence.slot_id and absence.slot_id.school_id != absence.school_id:
                raise UserError(_("This period belongs to another school."))
        absences._school_apply_to_sheets()
        return absences

    def write(self, vals):
        self._school_guard_write(vals)
        if not self.env.su and {"student_id", "date", "slot_id", "school_id"} & set(vals):
            raise UserError(_("An absence notice is not moved: delete it and declare another."))
        return super().write(vals)

    def _school_covers(self, day, slot):
        self.ensure_one()
        return self.date == day and (not self.slot_id or self.slot_id == slot)

    def _school_apply_to_sheets(self):
        """🔴 The educator of the day knows at once: the open sheets mark the child absent and say so."""
        Line = self.env["bf.school.childcare.sheet.line"].sudo()
        for absence in self:
            lines = Line.search([("student_id", "=", absence.student_id.id), ("date", "=", absence.date),
                                 ("sheet_id.state", "=", "open"), ("presence", "=", "expected")])
            lines = lines.filtered(lambda l: absence._school_covers(l.date, l.slot_id))
            lines.write({"presence": "absent"})
            for sheet in lines.sheet_id:
                sheet.message_post(body=_("Absence notice for %(student)s: %(reason)s",
                                          student=absence.student_id.name,
                                          reason=absence.reason or _("no reason given")))

    # --- The family's side (portal) ---------------------------------------------------------

    @api.model
    def _school_declare_by(self, partner, student, day, slot=None, reason=None):
        if student not in partner._school_authority_links().student_id:
            raise AccessError(_("Only an adult with parental authority declares an absence."))
        school = student._childcare_school(day)
        if not school:
            raise UserError(_("Child care is for the preschool and elementary students of the school."))
        if day < school._calendar_today():
            raise UserError(_("An absence is declared for today or later."))
        if slot and slot.school_id != school:
            raise UserError(_("This period belongs to another school."))
        with self.env.cr.savepoint():
            return self.sudo().create({
                "student_id": student.id, "school_id": school.id, "date": day, "slot_id": slot.id if slot else False,
                "reason": (reason or "").strip()[:200], "declared_by_id": partner.id, "channel": "portal"})


class AbsenceDeclaration(models.Model):
    _inherit = "bf.school.absence.declaration"

    @api.model_create_multi
    def create(self, vals_list):
        declarations = super().create(vals_list)
        declarations._school_childcare_apply()
        return declarations

    def _school_childcare_apply(self):
        """A whole-day absence declared to the school is also an absence at child care."""
        Line = self.env["bf.school.childcare.sheet.line"].sudo()
        for declaration in self.filtered(lambda d: d.part == "full"):
            lines = Line.search([("student_id", "=", declaration.student_id.id),
                                 ("date", ">=", declaration.date_from), ("date", "<=", declaration.date_to),
                                 ("sheet_id.state", "=", "open"), ("presence", "=", "expected")])
            lines.write({"presence": "absent"})
            for sheet in lines.sheet_id:
                sheet.message_post(body=_("Absence declared to the school for %s.", declaration.student_id.name))
