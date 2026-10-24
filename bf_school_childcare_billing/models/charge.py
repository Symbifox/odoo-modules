from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import clean_context, float_round, format_date

CHARGE_KINDS = [
    ("regular", "Regular registration"),
    ("slot", "Occasional period"),
    ("day", "Occasional day"),
    ("full_day", "Day without class"),
    ("walk_in", "Child not booked"),
    ("late_notice", "Short notice supplement"),
    ("late_pickup", "Late pick-up"),
    ("activity", "Activity fee"),
]
#: A ticket pays one of these, before anything else: an occasional period, or an occasional day when
#: the school prices occasional child care by the day.
TICKET_KINDS = ("slot", "day")
#: What a charge is born with, written every time: 🔴 the clicker's `default_*` context and own
#: ir.default would otherwise give a charge born "billed" without an invoice.
CHARGE_DEFAULTS = {"state": "open", "ticket_book_id": False, "invoice_ids": [(6, 0, [])], "waived_reason": False,
                   "waived_by_id": False, "booking_id": False, "line_id": False, "registration_id": False,
                   "activity_id": False, "slot_id": False, "late_minutes": 0}
CHARGE_STATES = [("open", "To bill"), ("ticket", "Paid by a ticket"), ("billed", "Billed"), ("waived", "Waived")]


def month_bounds(month):
    """The first and last day of the month of `month`."""
    start = month.replace(day=1)
    return start, start + relativedelta(months=1, days=-1)


class ChildcareCharge(models.Model):
    """One amount due for child care: the ledger the monthly invoices are made from.

    Computed again from what happened (bookings, sheets, registrations) until it is billed, paid
    by a ticket or waived; never changed after. Nobody creates or edits a charge by hand: a
    mistake already billed is corrected by a credit note in the accounting.
    """

    _name = "bf.school.childcare.charge"
    _description = "Child care charge"
    _order = "date desc, student_id, id"

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict", index=True)
    school_id = fields.Many2one("bf.school", "School", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="restrict", index=True)
    date = fields.Date("Service date", required=True, index=True)
    month = fields.Date(compute="_compute_month", store=True, index=True, help="The first day of the month served.")
    kind = fields.Selection(CHARGE_KINDS, required=True)
    slot_id = fields.Many2one("bf.school.childcare.slot", "Period", ondelete="restrict")
    quantity = fields.Float(default=1.0, digits=(16, 2))
    amount = fields.Monetary(required=True)
    eligible_amount = fields.Monetary("Child care (Relevé 24)", help="The part of the amount that is child care.")
    late_minutes = fields.Integer("Minutes late")
    booking_id = fields.Many2one("bf.school.childcare.booking", "Booking", ondelete="set null", index="btree_not_null")
    line_id = fields.Many2one("bf.school.childcare.sheet.line", "Attendance", ondelete="set null",
                              index="btree_not_null")
    registration_id = fields.Many2one("bf.school.childcare.registration", "Registration", ondelete="set null",
                                      index="btree_not_null")
    activity_id = fields.Many2one("bf.school.childcare.activity", "Activity", ondelete="set null")
    key = fields.Char(required=True, readonly=True, help="What the charge is for: a source is charged once.")
    state = fields.Selection(CHARGE_STATES, default="open", required=True, index=True)
    ticket_book_id = fields.Many2one("bf.school.childcare.ticket_book", "Paid by the book", ondelete="restrict",
                                     index="btree_not_null")
    invoice_ids = fields.Many2many("account.move", string="Invoices", copy=False)
    invoice_names = fields.Char("Invoice numbers", compute="_compute_invoice_names")
    waived_reason = fields.Char("Why waived")
    waived_by_id = fields.Many2one("res.users", "Waived by")

    _sql_constraints = [("key_unique", "UNIQUE(key)", "A source is charged once.")]

    @api.depends("date")
    def _compute_month(self):
        for charge in self:
            charge.month = charge.date and charge.date.replace(day=1)

    @api.depends("kind", "date", "slot_id")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for charge in self:
            charge.display_name = charge._school_label(self.env)

    def _compute_invoice_names(self):
        # Read in sudo: the office has no access to the invoices themselves.
        for charge in self:
            charge.invoice_names = ", ".join(charge.sudo().invoice_ids.mapped("name"))

    def _school_label(self, env):
        """What the charge is for, in the language of `env`."""
        self.ensure_one()
        kinds = dict(self._fields["kind"]._description_selection(env))
        day = format_date(env, self.date)
        if self.kind == "regular":
            return env._("%(kind)s, %(month)s", kind=kinds["regular"],
                         month=format_date(env, self.date, date_format="MMMM yyyy"))
        if self.kind == "late_pickup":
            return env._("%(kind)s, %(date)s, %(minutes)s min", kind=kinds["late_pickup"], date=day,
                         minutes=self.late_minutes)
        if self.kind == "activity":
            return env._("%(kind)s, %(date)s: %(activity)s", kind=kinds["activity"], date=day,
                         activity=self.sudo().activity_id.name or "")
        return ", ".join(filter(None, [kinds[self.kind], self.sudo().slot_id.name, day]))

    # --- Nobody writes the ledger by hand ---------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(_("The charges come from the bookings, the sheets and the registrations."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise UserError(_("A charge is not changed by hand: waive it, or correct the invoice with a "
                              "credit note."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_("A charge is not deleted: waive it."))
        if self.filtered(lambda c: c.state in ("billed", "ticket")):
            raise UserError(_("A charge billed or paid by a ticket stays."))
        return super().unlink()

    def action_waive(self, reason=None):
        """The school forgives a charge not billed yet (its own mistake, a gesture)."""
        self.check_access("write")
        if not (reason or "").strip():
            raise UserError(_("Say why the charge is waived."))
        if self.filtered(lambda c: c.state != "open"):
            raise UserError(_("Only a charge still to bill is waived."))
        self.sudo().write({"state": "waived", "waived_reason": reason.strip(), "waived_by_id": self.env.uid})
        return True

    def action_open_waive(self):
        return {
            "type": "ir.actions.act_window", "res_model": "bf.school.childcare.charge.waive", "view_mode": "form",
            "target": "new", "context": {"active_ids": self.ids},
        }

    # --- The ledger of a month --------------------------------------------------------------------

    @api.model
    def _school_compute(self, school, month):
        """Make the ledger of `school` for the month of `month` agree with what happened.

        Returns what could not be priced: [(kind, period)] without a rate, registrations without a
        tier. A charge billed, paid by a ticket or waived is never touched again.
        """
        start, end = month_bounds(month)
        Year = self.env["bf.school.year"].sudo()
        Rate, Tier = self.env["bf.school.childcare.rate"], self.env["bf.school.childcare.tier"]
        years = {}

        def year_of(day):
            if day not in years:
                years[day] = Year.search([("school_id", "=", school.id), ("date_start", "<=", day),
                                          ("date_end", ">=", day)], limit=1)
            return years[day]

        wanted, missing = {}, set()

        def want(key, student, day, kind, amount, rate=None, eligible=None, units=1.0, **extra):
            year = year_of(day)
            if not year:
                return
            amount = float_round(amount, 2)
            if eligible is None:
                eligible = rate._school_eligible(amount, units) if rate else 0.0
            wanted[key] = dict(CHARGE_DEFAULTS, student_id=student.id, school_id=school.id, year_id=year.id, date=day,
                               kind=kind, quantity=units, amount=amount,
                               eligible_amount=float_round(min(eligible, amount), 2), key=key, **extra)

        def occasional(key, student, day, slot, **extra):
            """A period taken without a regular registration: the day rate when the school has one."""
            year = year_of(day)
            if not year:
                return
            if slot.kind == "full_day":
                rate = Rate._school_rate(school, year, "full_day", slot)
                kind = "full_day"
            else:
                rate = Rate._school_rate(school, year, "day")
                kind = "day" if rate else "slot"
                if rate:
                    key = "day:%s:%s" % (student.id, day)
                else:
                    rate = Rate._school_rate(school, year, "slot", slot)
            if not rate:
                missing.add((kind, slot.name))
                return
            want(key, student, day, kind, rate.amount, rate, slot_id=slot.id if kind != "day" else False, **extra)

        Booking = self.env["bf.school.childcare.booking"].sudo()
        bookings = Booking.search([
            ("school_id", "=", school.id), ("date", ">=", start), ("date", "<=", end),
            "|", ("state", "=", "booked"), "&", ("state", "=", "cancelled"), ("cancel_late", "=", True)],
            order="date, id")
        for booking in bookings:
            student, day, slot = booking.student_id, booking.date, booking.slot_id
            occasional("booking:%s" % booking.id, student, day, slot, booking_id=booking.id)
            if booking.late_notice:
                rate = Rate._school_rate(school, year_of(day), "late_notice") if year_of(day) else None
                if rate:
                    want("booking:%s:late_notice" % booking.id, student, day, "late_notice", rate.amount, rate,
                         booking_id=booking.id, slot_id=slot.id)
                elif year_of(day):
                    missing.add(("late_notice", ""))
            activity = booking.activity_id
            if activity and activity.fee:
                want("booking:%s:activity" % booking.id, student, day, "activity", activity.fee,
                     eligible=activity.fee if activity.fee_rl24 else 0.0, booking_id=booking.id,
                     activity_id=activity.id, slot_id=slot.id)

        Line = self.env["bf.school.childcare.sheet.line"].sudo()
        lines = Line.search([("school_id", "=", school.id), ("date", ">=", start), ("date", "<=", end),
                             "|", "&", ("origin", "=", "walk_in"), ("presence", "=", "present"),
                             ("late_minutes", ">", 0)], order="date, id")
        for line in lines:
            student, day, slot, year = line.student_id, line.date, line.slot_id, year_of(line.date)
            if not year:
                continue
            if line.origin == "walk_in" and line.presence == "present":
                rate = Rate._school_rate(school, year, "walk_in", slot)
                if rate:
                    want("line:%s:walk_in" % line.id, student, day, "walk_in", rate.amount, rate, line_id=line.id,
                         slot_id=slot.id)
                else:
                    # No price for a child not booked: the occasional one.
                    occasional("line:%s:walk_in" % line.id, student, day, slot, line_id=line.id)
            if line.late_minutes > 0:
                rate = Rate._school_rate(school, year, "late_pickup")
                if rate:
                    want("line:%s:late_pickup" % line.id, student, day, "late_pickup",
                         rate._school_late_amount(line.late_minutes), rate, line_id=line.id, slot_id=slot.id,
                         late_minutes=line.late_minutes)
                else:
                    missing.add(("late_pickup", ""))

        if school.childcare_billing == "monthly":
            class_days = [day.date for day in self.env["bf.school.calendar.day"].sudo().search([
                ("school_id", "=", school.id), ("date", ">=", start), ("date", "<=", end),
                ("kind", "=", "class"), ("childcare_open", "=", True)])]
            registrations = self.env["bf.school.childcare.registration"].sudo().search([
                ("school_id", "=", school.id), ("state", "in", ("active", "ended")),
                ("date_start", "<=", end), ("date_end", ">=", start)])
            for registration in registrations:
                # A regular day without class (pedagogical day) is billed at its own rate, on the days it runs.
                for line in registration.line_ids.filtered(lambda l: l.slot_id.kind == "full_day"):
                    day = max(start, registration.date_start)
                    while day <= min(end, registration.date_end):
                        if line._weekday_ticked(day) and line.slot_id._runs_on(day) \
                                and school._calendar_childcare_open(day):
                            occasional("registration:%s:%s:%s" % (registration.id, line.slot_id.id, day),
                                       registration.student_id, day, line.slot_id, registration_id=registration.id)
                        day += relativedelta(days=1)
                # The tier counts the periods of the class days.
                periods = sum(line._school_day_count() for line in registration.line_ids
                              if line.slot_id.kind != "full_day")
                covered = [d for d in class_days if registration.date_start <= d <= registration.date_end]
                if not covered or not periods:
                    continue
                tier = Tier._school_tier(school, registration.year_id, periods)
                if not tier:
                    missing.add(("regular", str(periods)))
                    continue
                # A month begun or ended mid-way is prorated on its class days.
                amount = float_round(tier.amount * len(covered) / len(class_days), 2)
                want("registration:%s:%s" % (registration.id, start), registration.student_id, max(start, min(covered)),
                     "regular", amount, eligible=amount if tier.rl24 else 0.0, registration_id=registration.id)

        # 🔴 Written in a clean context: no `default_*` of the caller on the charges.
        Charges = self.sudo().with_context(clean_context(self.env.context))
        existing = Charges.search([("school_id", "=", school.id), ("month", "=", start)])
        by_key = {charge.key: charge for charge in existing}
        fields_compared = ("amount", "eligible_amount", "quantity", "kind", "slot_id", "late_minutes", "year_id")
        new = []
        for key, vals in wanted.items():
            charge = by_key.get(key)
            if not charge:
                new.append(vals)
            elif charge.state == "open":
                changes = {f: vals.get(f, False) for f in fields_compared
                           if (charge[f].id if hasattr(charge[f], "id") else charge[f]) != vals.get(f, False)}
                if changes:
                    charge.write(changes)
        # 🔴 A key taken in another month is not a new charge (a booking moved to another date).
        taken = set(Charges.search([("key", "in", [v["key"] for v in new])]).mapped("key"))
        Charges.create([vals for vals in new if vals["key"] not in taken])
        gone = existing.filtered(lambda c: c.state in ("open", "ticket") and c.key not in wanted)
        # A period paid by a ticket that is no longer due (day closed, activity cancelled): the ticket comes back.
        gone.filtered(lambda c: c.state == "ticket").write({"state": "open", "ticket_book_id": False})
        gone.unlink()
        self._school_apply_tickets(school, start)
        return sorted(missing)

    @api.model
    def _school_apply_tickets(self, school, month):
        """The paid books of tickets pay the occasional periods of the month, oldest book first."""
        start, end = month_bounds(month)
        charges = self.sudo().search([("school_id", "=", school.id), ("state", "=", "open"),
                                      ("kind", "in", TICKET_KINDS), ("date", ">=", start), ("date", "<=", end)],
                                     order="date, id")
        by_student = defaultdict(lambda: charges.browse())
        for charge in charges:
            by_student[(charge.student_id, charge.year_id)] |= charge
        Book = self.env["bf.school.childcare.ticket_book"].sudo()
        for (student, year), todo in by_student.items():
            # 🔴 A book whose invoice was credited is not paid any more, whatever its state says.
            books = Book.search([("student_id", "=", student.id), ("school_id", "=", school.id),
                                 ("year_id", "=", year.id), ("state", "=", "paid")], order="id").filtered("usable")
            for book in books:
                # 🔴 Taken under the book's row lock: two runs at once would spend the same ticket.
                book._school_lock()
                left = book._school_left()
                paid, todo = todo[:max(left, 0)], todo[max(left, 0):]
                paid.write({"state": "ticket", "ticket_book_id": book.id})
                if not todo:
                    break


class ChildcareChargeWaive(models.TransientModel):
    _name = "bf.school.childcare.charge.waive"
    _description = "Waive child care charges"

    reason = fields.Char("Why", required=True)

    def action_waive(self):
        charges = self.env["bf.school.childcare.charge"].browse(self.env.context.get("active_ids", []))
        charges.action_waive(self.reason)
        return {"type": "ir.actions.act_window_close"}
