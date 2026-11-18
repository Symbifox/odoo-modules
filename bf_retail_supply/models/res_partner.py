# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

MODES = [
    ("van", "A van to the store"),
    ("depot", "Collected from a depot"),
]


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_retail_supplier = fields.Boolean(
        "Retail supplier",
        help="Its order guide is offered in Retail, and its price file can be read.")
    bf_retail_delivery_day_ids = fields.Many2many(
        "bf.retail.weekday", "bf_retail_partner_weekday_rel", "partner_id", "weekday_id",
        string="Delivers on", help="Left empty: every day counts as a delivery day.")
    bf_retail_order_cutoff = fields.Float(
        "Order by", help="The hour of the day, in the store's own time zone, past which an "
                         "order no longer makes the next delivery. 0: no cut-off.")
    bf_retail_lead_days = fields.Integer(
        "Days to deliver", default=1,
        help="Days between an order placed before the cut-off and the delivery it makes.")
    bf_retail_min_order = fields.Monetary(
        "Minimum order", currency_field="bf_retail_currency_id",
        help="The order guide says so when the total is below it. It does not refuse.")
    bf_retail_currency_id = fields.Many2one(
        "res.currency", compute="_compute_bf_retail_currency", string="Currency")
    bf_retail_supply_mode = fields.Selection(MODES, "How it arrives", default="van")
    bf_retail_rep_id = fields.Many2one(
        "res.partner", "Representative", domain="[('is_company', '=', False)]",
        help="Who to call about an order, a shortage or a credit.")
    bf_retail_returns_credited = fields.Boolean(
        "Credits what did not sell",
        help="Bread, milk and magazines are often on sale or return. What came back shows in "
             "the order guide either way; this says whether it cost the store anything.")

    @api.constrains("bf_retail_order_cutoff")
    def _check_bf_retail_order_cutoff(self):
        """An hour of the day, and nothing else.

        A negative hour was always past, so every order missed the next van; 25 was never
        reached, so the cut-off did nothing at all. Neither said a word.
        """
        for partner in self:
            if not 0.0 <= partner.bf_retail_order_cutoff < 24.0:
                raise ValidationError(_(
                    "The hour to order by is an hour of the day, from 0:00 to 23:59."))

    @api.depends_context("company")
    def _compute_bf_retail_currency(self):
        currency = self.env.company.currency_id
        for partner in self:
            partner.bf_retail_currency_id = currency

    def _bf_retail_tz(self, warehouse=None):
        """The store's time zone: the store's own address, else the company's, else the
        user's, else UTC.

        🔴 ONE resolution for ONE decision. The guide used to read company, then warehouse,
        then user, and the supplier company then user: with a store that carries its own
        address, the delivery day was decided in Auckland and "today" in Toronto, and a
        delivery could fall before the "today" of the very same guide.
        """
        self.ensure_one()
        name = (warehouse.partner_id.tz if warehouse else False) \
            or self.env.company.partner_id.tz or self.env.user.tz or "UTC"
        return pytz.timezone(name)

    def _bf_retail_deliveries(self, now=None, warehouse=None):
        """(the next delivery this order can make, the one after it).

        The quantity to order covers until the delivery AFTER the next one: ordering only
        what is needed until tomorrow leaves the shelf empty the day after, which is how a
        corner store runs out of milk on a Sunday.

        The cut-off is read in the store's time zone, not in UTC: an order placed at 19:00
        in Auckland is not an order placed at 07:00 the same day.
        """
        self.ensure_one()
        tz = self._bf_retail_tz(warehouse)
        now = now or datetime.now(pytz.utc).astimezone(tz)
        if now.tzinfo is None:
            now = tz.localize(now)
        today = now.date()
        lead = max(self.bf_retail_lead_days, 0)
        earliest = today + timedelta(days=lead)
        cutoff = self.bf_retail_order_cutoff or 0.0
        if cutoff:
            passed = now.hour + now.minute / 60.0 >= cutoff
            if passed:
                earliest += timedelta(days=1)
        days = set(self.bf_retail_delivery_day_ids.mapped("code"))
        if not days:
            # No day ticked means every day delivers, which the field says in so many
            # words: the delivery AFTER the next one is the day after, not a week later.
            # A week later is seven days of shelf to cover, and the quantity suggested on
            # a brand new supplier was out by that factor.
            return earliest, earliest + timedelta(days=1)
        first = self._bf_retail_next_day(earliest, days)
        return first, self._bf_retail_next_day(first + timedelta(days=1), days)

    @staticmethod
    def _bf_retail_next_day(start, days):
        """The first day on or after `start` that falls on one of these weekdays."""
        for step in range(8):
            day = start + timedelta(days=step)
            if day.weekday() in days:
                return day
        return start

    def _bf_retail_local_noon(self, day, warehouse=None):
        """Noon of `day` in the store's time zone, in UTC.

        A date at midnight UTC reads as the day before west of Greenwich, and the request
        for quotation sent to the supplier would name the wrong day.
        """
        self.ensure_one()
        tz = self._bf_retail_tz(warehouse)
        return tz.localize(datetime.combine(day, time(12, 0))).astimezone(
            pytz.utc).replace(tzinfo=None)
