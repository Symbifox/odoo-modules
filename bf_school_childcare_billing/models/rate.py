import math

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_round

RATE_KINDS = [
    ("slot", "Occasional period"),
    ("day", "Occasional day"),
    ("full_day", "Day without class"),
    ("walk_in", "Child not booked"),
    ("late_notice", "Short notice supplement"),
    ("late_pickup", "Late pick-up"),
    ("ticket_book", "Book of tickets"),
]
#: A rate of these kinds may be set for one period; without one, it applies to every period.
PER_SLOT_KINDS = ("slot", "full_day", "walk_in")
#: Late pick-up is a penalty, not child care, unless the school decides otherwise.
NOT_CARE_KINDS = ("late_pickup",)


class ChildcareRate(models.Model):
    """A price of the school's child care for a school year."""

    _name = "bf.school.childcare.rate"
    _description = "Child care rate"
    _order = "school_id, year_id desc, kind, slot_id, id"

    school_id = fields.Many2one("bf.school", "School", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="restrict", index=True,
                              domain="[('school_id', '=', school_id)]")
    kind = fields.Selection(RATE_KINDS, required=True, default="slot")
    slot_id = fields.Many2one(
        "bf.school.childcare.slot", "Period", ondelete="restrict", domain="[('school_id', '=', school_id)]",
        help="Empty: every period of the school. A rate for one period wins over the general one.")
    amount = fields.Monetary(
        required=True, help="The price of one unit. Late pick-up: the first block. Book of tickets: the book.")
    block_minutes = fields.Integer("First block (minutes)", default=15,
                                   help="Late pick-up: the minutes the first block covers.")
    next_block_minutes = fields.Integer("Next blocks (minutes)", default=15,
                                        help="Late pick-up: the length of each block after the first.")
    next_block_amount = fields.Monetary("Next blocks", help="Late pick-up: the price of each block after "
                                                            "the first. Empty: the price of the first block.")
    tickets = fields.Integer("Tickets in the book", help="Book of tickets: one ticket pays one occasional period, or "
                                                         "one occasional day when the school prices by the day.")
    rl24 = fields.Boolean(
        "Child care (Relevé 24)", compute="_compute_rl24", store=True, readonly=False,
        help="What is paid at this rate goes on the payer's Relevé 24. Ticked by default, except for late "
             "pick-up.")
    rl24_cap = fields.Monetary(
        "Eligible per unit", help="The most of one unit that is child care (an outing day of 47 $ whose "
                                  "child care is 22 $). Empty: the whole price.")
    active = fields.Boolean(default=True)

    @api.depends("kind")
    def _compute_rl24(self):
        for rate in self:
            rate.rl24 = rate.kind not in NOT_CARE_KINDS

    @api.depends("kind", "slot_id", "amount", "year_id")
    @api.depends_context("lang")
    def _compute_display_name(self):
        kinds = dict(self._fields["kind"]._description_selection(self.env))
        for rate in self:
            rate.display_name = " · ".join(filter(None, [
                rate.year_id.name, kinds.get(rate.kind), rate.slot_id.name]))

    @api.constrains("school_id", "year_id", "slot_id", "kind", "amount", "block_minutes", "next_block_minutes",
                    "next_block_amount", "tickets", "rl24_cap", "active")
    def _check_rate(self):
        for rate in self:
            if rate.year_id.school_id != rate.school_id or (rate.slot_id and rate.slot_id.school_id != rate.school_id):
                raise ValidationError(_("A rate, its school year and its period belong to the same school."))
            if rate.slot_id and rate.kind not in PER_SLOT_KINDS:
                raise ValidationError(_("This rate applies to the whole school: leave the period empty."))
            if min(rate.amount, rate.next_block_amount, rate.rl24_cap) < 0:
                raise ValidationError(_("A price is zero or more."))
            if rate.kind == "late_pickup" and (rate.block_minutes <= 0 or rate.next_block_minutes <= 0):
                raise ValidationError(_("A block of late pick-up lasts at least one minute."))
            if rate.kind == "ticket_book" and rate.tickets <= 0:
                raise ValidationError(_("A book holds at least one ticket."))
            if rate.active and self.search_count([
                    ("id", "!=", rate.id), ("school_id", "=", rate.school_id.id), ("year_id", "=", rate.year_id.id),
                    ("kind", "=", rate.kind), ("slot_id", "=", rate.slot_id.id)], limit=1):
                raise ValidationError(_("This rate already exists for that year and period: change it or archive it."))

    @api.model
    def _school_rate(self, school, year, kind, slot=None):
        """The rate of a period, else the school's general one, read in sudo; empty when none."""
        Rate = self.sudo()
        base = [("school_id", "=", school.id), ("year_id", "=", year.id), ("kind", "=", kind)]
        if slot:
            rate = Rate.search(base + [("slot_id", "=", slot.id)], limit=1)
            if rate:
                return rate
        return Rate.search(base + [("slot_id", "=", False)], limit=1)

    def _school_eligible(self, amount, units=1):
        """The child-care part of `amount` paid for `units` units at this rate."""
        self.ensure_one()
        if not self.rl24:
            return 0.0
        if self.rl24_cap:
            return float_round(min(amount, self.rl24_cap * units), 2)
        return amount

    def _school_late_amount(self, minutes):
        """The price of `minutes` late: the first block, then each block begun after it."""
        self.ensure_one()
        if minutes <= 0:
            return 0.0
        extra = max(0, minutes - self.block_minutes)
        blocks = math.ceil(extra / self.next_block_minutes) if extra else 0
        return float_round(self.amount + blocks * (self.next_block_amount or self.amount), 2)


class ChildcareTier(models.Model):
    """The monthly price of a regular registration, by its number of periods per week."""

    _name = "bf.school.childcare.tier"
    _description = "Monthly child care tier"
    _order = "school_id, year_id desc, min_periods, id"

    school_id = fields.Many2one("bf.school", "School", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="restrict", index=True,
                              domain="[('school_id', '=', school_id)]")
    min_periods = fields.Integer("From (periods per week)", required=True, default=1)
    max_periods = fields.Integer("To (periods per week)", required=True, default=5)
    amount = fields.Monetary("Per month", required=True)
    rl24 = fields.Boolean("Child care (Relevé 24)", default=True)

    @api.depends("min_periods", "max_periods", "year_id")
    def _compute_display_name(self):
        for tier in self:
            tier.display_name = _("%(year)s · %(low)s to %(high)s periods a week", year=tier.year_id.name or "",
                                  low=tier.min_periods, high=tier.max_periods)

    @api.constrains("school_id", "year_id", "min_periods", "max_periods", "amount")
    def _check_tier(self):
        for tier in self:
            if tier.year_id.school_id != tier.school_id:
                raise ValidationError(_("A tier and its school year belong to the same school."))
            if not 1 <= tier.min_periods <= tier.max_periods or tier.amount < 0:
                raise ValidationError(_("A tier goes from one period a week or more, to as many or more, at a "
                                        "price of zero or more."))
            if self.search_count([
                    ("id", "!=", tier.id), ("school_id", "=", tier.school_id.id), ("year_id", "=", tier.year_id.id),
                    ("min_periods", "<=", tier.max_periods), ("max_periods", ">=", tier.min_periods)], limit=1):
                raise ValidationError(_("Two tiers of the same year cover the same number of periods."))

    @api.model
    def _school_tier(self, school, year, periods):
        return self.sudo().search([("school_id", "=", school.id), ("year_id", "=", year.id),
                                   ("min_periods", "<=", periods), ("max_periods", ">=", periods)], limit=1)
