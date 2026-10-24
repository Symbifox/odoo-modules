from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .common import DAY_FIELDS, time_label
from .school import CHILDCARE_STAGES

SLOT_KINDS = [
    ("before", "Before school"),
    ("noon", "Lunch time"),
    ("after", "After school"),
    ("full_day", "Day without class"),
]
#: These periods run on class days; a day without class (pedagogical day, open break) runs
#: the full-day periods.
CLASS_DAY_KINDS = ("before", "noon", "after")
#: Changing one of these may stop the period on dates already booked.
RUN_FIELDS = {"kind", "active", "mon", "tue", "wed", "thu", "fri"}
#: The records that tie a period to its school.
USES = ("bf.school.childcare.booking", "bf.school.childcare.registration.line", "bf.school.childcare.sheet")
#: Children leave these periods for their class, not with an adult.
TO_CLASS_KINDS = ("before", "noon")


class ChildcareSlot(models.Model):
    """A period of child care at a school: before school, lunch time, after school, or a day
    without class. Its places are counted per date."""

    _name = "bf.school.childcare.slot"
    _inherit = ["bf.school.childcare.weekdays"]
    _description = "Child care period"
    _order = "school_id, sequence, start_time, id"

    name = fields.Char(required=True, help="For example: After school, or Pedagogical day.")
    sequence = fields.Integer(default=10)
    school_id = fields.Many2one("bf.school", "School", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True, index=True)
    kind = fields.Selection(SLOT_KINDS, required=True, default="after",
                            help="Before school, lunch time and after school run on class days; "
                                 "a day without class runs on the open days without class.")
    start_time = fields.Float("Starts", required=True)
    end_time = fields.Float("Ends", required=True)
    mon = fields.Boolean(default=True)
    tue = fields.Boolean(default=True)
    wed = fields.Boolean(default=True)
    thu = fields.Boolean(default=True)
    fri = fields.Boolean(default=True)
    level_ids = fields.Many2many(
        "bf.school.level", string="Levels", domain=[("stage", "in", CHILDCARE_STAGES)],
        help="Empty: every preschool and elementary level.")
    capacity = fields.Integer("Places", help="Children at most on one date. 0: no limit.")
    grace_minutes = fields.Integer(
        "Grace (minutes)", help="Minutes after the end before a pick-up counts as late.")
    active = fields.Boolean(default=True)

    @api.depends("name", "start_time", "end_time")
    def _compute_display_name(self):
        for slot in self:
            slot.display_name = "%s (%s)" % (slot.name or "", slot._hours_label())

    def _hours_label(self):
        self.ensure_one()
        return "%s-%s" % (time_label(self.start_time), time_label(self.end_time))

    @api.constrains("start_time", "end_time", "capacity", "grace_minutes")
    def _check_values(self):
        for slot in self:
            if not 0 <= slot.start_time < slot.end_time <= 24:
                raise ValidationError(_("A period ends after it starts, within the day."))
            if slot.capacity < 0 or slot.grace_minutes < 0:
                raise ValidationError(_("The places and the grace minutes are zero or more."))

    @api.constrains("level_ids")
    def _check_levels(self):
        for slot in self:
            if slot.level_ids.filtered(lambda level: level.stage not in CHILDCARE_STAGES):
                raise ValidationError(_("Child care is for preschool and elementary levels only."))

    def write(self, vals):
        # A period already used is not moved to another school: its bookings, registrations and
        # sheets would follow it.
        if "school_id" in vals and any(self.env[model].sudo().search_count([("slot_id", "in", self.ids)])
                                       for model in USES):
            raise UserError(_("A period already used stays at its school: archive it and create another."))
        result = super().write(vals)
        if RUN_FIELDS & set(vals):
            self._school_close_stranded()
        return result

    def _school_close_stranded(self):
        """🔴 The period no longer runs on some booked dates (a day unticked, a kind changed, the
        period archived): those bookings are closed, nothing due, instead of staying due and
        invisible to the family."""
        Booking = self.env["bf.school.childcare.booking"].sudo()
        for slot in self:
            today = slot.school_id._calendar_today()
            Booking.search([("slot_id", "=", slot.id), ("state", "=", "booked"), ("date", ">=", today)]).filtered(
                lambda b: not slot._runs_on(b.date))._school_close()

    # --- When it runs, for whom -------------------------------------------------------------

    def _runs_on(self, day):
        """Does this period run on `day`? The school's calendar decides; a date it does not hold
        is closed."""
        self.ensure_one()
        if not self.active or not self._weekday_ticked(day):
            return False
        calendar_day = self.school_id._calendar_day(day)
        if not calendar_day.childcare_open:
            return False
        if self.kind == "full_day":
            return calendar_day.kind != "class"
        return calendar_day.kind == "class"

    def _accepts(self, student, day):
        """The student may attend this period: a preschool or elementary student of the school,
        at one of the period's levels."""
        self.ensure_one()
        levels = student._childcare_levels(self.school_id, day)
        return bool(levels) and (not self.level_ids or bool(levels & self.level_ids))

    def _school_start(self, day):
        """When the period starts on `day`, in UTC (naive), read on the school's clock."""
        self.ensure_one()
        return self.school_id._calendar_at(day, self.start_time)

    def _school_end(self, day):
        self.ensure_one()
        return self.school_id._calendar_at(day, self.end_time)

    # --- Places ---------------------------------------------------------------------------

    def _school_lock(self):
        """Serialize the places of these periods.

        🔴 A row lock alone does not do it: under REPEATABLE READ, the transaction that waited
        for the lock still reads the bookings of its old snapshot, and two families both took
        the last place. UPDATING the period's row does: the second transaction fails to
        serialize, Odoo replays it, and the replay sees the first booking (same as the meal
        account).
        """
        if self.ids:
            self.env.cr.execute("UPDATE bf_school_childcare_slot SET write_date = now() at time zone 'UTC' "
                                "WHERE id IN %s", [tuple(self.ids)])

    def _school_registered(self, day):
        """The lines of the registrations in force on `day` that cover this period that weekday."""
        self.ensure_one()
        if day.weekday() >= 5:
            return self.env["bf.school.childcare.registration.line"]
        return self.env["bf.school.childcare.registration.line"].sudo().search([
            ("slot_id", "=", self.id), ("registration_id.state", "in", ("active", "ended")),
            ("registration_id.date_start", "<=", day), ("registration_id.date_end", ">=", day),
            (DAY_FIELDS[day.weekday()], "=", True)])

    def _school_booked(self, day):
        self.ensure_one()
        return self.env["bf.school.childcare.booking"].sudo().search([
            ("slot_id", "=", self.id), ("date", "=", day), ("state", "=", "booked")])

    def _school_activity(self, day):
        """The activity planned for this full-day period on `day`, if any (cancelled ones aside)."""
        self.ensure_one()
        return self.env["bf.school.childcare.activity"].sudo().search([
            ("slot_id", "=", self.id), ("date", "=", day), ("state", "!=", "cancelled")], limit=1)

    def _school_capacity(self, day):
        """The places of this period on `day`: its own, or an activity's when lower. 0: no limit."""
        self.ensure_one()
        capacity = self.capacity
        places = self._school_activity(day).places
        if places:
            capacity = min(capacity, places) if capacity else places
        return capacity

    def _school_taken(self, day):
        """Places taken on `day`: the registered children and the active bookings."""
        self.ensure_one()
        return len(self._school_registered(day).registration_id.student_id) + len(self._school_booked(day))

    def _school_has_room(self, day, extra=1):
        self.ensure_one()
        capacity = self._school_capacity(day)
        return not capacity or self._school_taken(day) + extra <= capacity
