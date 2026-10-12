"""The kilometres of a route day driven with the worker's own car, claimed as a draft expense.

⚠️ The worker's phone finishes the day (``app_finish``); the expense is created there, in
sudo, for the worker's own employee only, and never blocks the end of the day: a claim that
fails, or cannot be made, leaves a note on the day for the office.
⚠️ Every path starts from a clean context (``clean_env``). ``sudo()`` already drops the
``default_*`` keys, but not the others: over RPC (``action_finish`` is public, the office
writes the day), ``mail_create_nolog`` would erase the expense's creation trace and
``mail_activity_quick_update`` would silence the activity to the person responsible.
⚠️ An expense already in a report (or refused) is never changed: whoever approves it has
seen its amount. Only drafts are redone when the office corrects an odometer.
⚠️ Two rates per calendar year (CRA and Revenu Québec): the full rate up to the threshold,
counting the mileage expenses of the employee dated BEFORE the day (routes or typed by hand,
refused ones excepted), then the reduced rate. When a day's claim changes, the drafts of the
later days of the year are redone, in date order, so each one counts what precedes it; a
draft the worker edited is left alone (a note says so).
⚠️ A reason that belongs to the settings (product, rate, employee) never deletes the worker's
own draft: a setting fixed later would not bring it back. Only the day's own facts do (no
kilometres, not an own vehicle), and so does a draft held by an employee who no longer drives
the day.
"""
import logging
from datetime import date

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import format_date, formatLang

from odoo.addons.bf_route.models.bf_route_day import clean_env

_logger = logging.getLogger(__name__)

# Fields of a finished day that change what it claims.
CLAIM_FIELDS = {"odometer_start", "odometer_end", "vehicle_id", "user_id", "date"}


class BfRouteDay(models.Model):
    _inherit = "bf.route.day"

    personal_vehicle = fields.Boolean(related="vehicle_id.bf_route_personal")
    expense_ids = fields.One2many("hr.expense", "bf_route_day_id", string="Mileage expenses",
                                  readonly=True)
    expense_count = fields.Integer(compute="_compute_expense_count")

    def _compute_expense_count(self):
        for day in self:
            day.expense_count = len(day.sudo().expense_ids)

    # ------------------------------------------------------------------
    # When
    # ------------------------------------------------------------------
    def action_finish(self, odometer=None, client_time=None):
        result = super().action_finish(odometer=odometer, client_time=client_time)
        self._bf_route_sync_mileage(alert=True)
        return result

    def write(self, vals):
        # The end of the day writes with bf_route_auto, then action_finish claims.
        claims = CLAIM_FIELDS & set(vals) and not self.env.context.get("bf_route_auto")
        # Given to someone else, or moved later: the drafts between the former point in time
        # and the new one (or of the former employee) counted this day's kilometres.
        before = {}
        if claims and ("user_id" in vals or "date" in vals):
            before = {day.id: (day.sudo()._bf_route_employee(), day.date)
                      for day in self if day.state == "done" and day.sudo().expense_ids}
        result = super().write(vals)
        if claims:
            self.filtered(lambda d: d.state == "done")._bf_route_sync_mileage()
            for day in self.filtered(lambda d: d.id in before):
                employee, when = before[day.id]
                current = day.sudo()._bf_route_employee()
                if employee and (employee != current or when < day.date):
                    later = day.sudo()._bf_route_later_drafts(employee=employee, after=(when, day.id))
                    (later - day)._bf_route_sync_mileage(cascade=False)
        return result

    def action_claim_mileage(self):
        """Office button: claim (or claim again) the kilometres of finished days."""
        self._check_manager()
        # The rules of the manager's own companies, before anything runs in sudo.
        self.check_access("write")
        for day in self:
            reason = day.sudo()._bf_route_no_claim_reason()[0]
            if reason:
                raise UserError(reason)
        self._bf_route_sync_mileage()
        return True

    def action_open_expenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Mileage expenses"),
            "res_model": "hr.expense",
            "view_mode": "list,form",
            "domain": [("bf_route_day_id", "=", self.id)],
            "context": {"create": False},
        }

    # ------------------------------------------------------------------
    # What
    # ------------------------------------------------------------------
    def _bf_route_employee(self):
        self.ensure_one()
        return self.user_id.sudo().employee_ids.filtered(
            lambda e: e.company_id == self.company_id and e.active)[:1]

    def _bf_route_mileage_products(self):
        self.ensure_one()
        company = self.company_id.sudo()
        full = company.bf_route_mileage_product_id or self.env.ref(
            "hr_expense.expense_product_mileage", raise_if_not_found=False)
        reduced = company.bf_route_mileage_reduced_product_id or self.env.ref(
            "bf_route_expense.product_mileage_reduced", raise_if_not_found=False)
        return full, reduced

    def _bf_route_usable(self, product):
        """An expense product the claim can use: active, expensable, with a rate here."""
        return bool(product and product.active and product.can_be_expensed
                    and product.with_company(self.company_id).standard_price > 0)

    def _bf_route_no_claim_reason(self):
        """(why this day claims nothing, in words for the office; whether the reason is in
        the settings rather than the day), or (False, False) when it claims."""
        self.ensure_one()
        if self.state != "done":
            return _("Only a finished day claims its kilometres."), False
        if not self.vehicle_id.bf_route_personal:
            return _("This day was not driven with an employee's own vehicle (Vehicle, "
                     "Employee's own vehicle): its kilometres stay in the mileage log."), False
        if self.distance_actual_km <= 0:
            return _("No kilometres: enter the odometer at the start and at the end."), False
        full, reduced = self._bf_route_mileage_products()
        if not self._bf_route_usable(full):
            return _("No mileage expense product with a rate: check the Routes settings."), True
        if self.company_id.sudo().bf_route_mileage_threshold > 0 and not self._bf_route_usable(reduced):
            return _("No reduced-rate mileage product with a rate: check the Routes settings, or set "
                     "the yearly threshold to 0."), True
        if not self._bf_route_employee():
            return _("%s has no employee in this company: no expense to claim for.",
                     self.user_id.name or "-"), True
        return False, False

    def _bf_route_earlier_domain(self, employee, products):
        """The employee's mileage expenses of the year dated before this day (same date: the
        ones typed by hand, and the days made before this one)."""
        self.ensure_one()
        return [
            ("employee_id", "=", employee.id),
            ("product_id", "in", products.ids),
            ("date", ">=", date(self.date.year, 1, 1)),
            ("state", "!=", "refused"),
            # A negative draft typed by hand would keep every kilometre at the full rate.
            ("quantity", ">", 0),
            "|", ("date", "<", self.date),
            "&", ("date", "=", self.date),
            "|", ("bf_route_day_id", "=", False), ("bf_route_day_id", "<", self.id),
        ]

    def _bf_route_mileage_lines(self, employee):
        """[(product, km)]: the kilometres at the full rate, then those beyond the threshold."""
        self.ensure_one()
        km = round(self.distance_actual_km, 1)
        full, reduced = self._bf_route_mileage_products()
        threshold = self.company_id.sudo().bf_route_mileage_threshold
        if threshold <= 0:
            return [(full, km)]
        earlier = self.env["hr.expense"].sudo().search(
            self._bf_route_earlier_domain(employee, full | reduced)).filtered(
            lambda e: e.bf_route_day_id != self)
        left = max(0.0, threshold - sum(earlier.mapped("quantity")))
        at_full = round(min(km, left), 1)
        lines = []
        if at_full > 0:
            lines.append((full, at_full))
        if km - at_full > 0:
            lines.append((reduced, round(km - at_full, 1)))
        return lines

    # ------------------------------------------------------------------
    # How
    # ------------------------------------------------------------------
    def _bf_route_sync_mileage(self, cascade=True, alert=False):
        """Make the claim of each finished day match its kilometres, then redo the drafts of
        the later days of the year it shifts (``cascade``: an argument, never a context key a
        caller could set). alert: the end of the day on the phone; a claim that cannot be
        made also gives the person responsible one activity, since nobody reads a note on a day
        that is over. Never raises: the end of a day on the phone must not fail on an expense."""
        days = clean_env(self).sudo()
        changed = days.browse()
        for day in days.sorted(lambda d: (d.date, d.id)):
            if day.state != "done":
                continue
            try:
                with self.env.cr.savepoint():
                    if day._bf_route_sync_one(keep_edits=not cascade, alert=alert):
                        changed |= day
            except (UserError, ValidationError, AccessError) as exc:
                _logger.info("bf_route_expense: day %s not claimed: %s", day.id, exc)
                text = exc.args[0] if exc.args else str(exc)
                day._bf_route_note(_("The kilometres were not claimed: %s", text))
                if alert:
                    day._bf_route_alert_unclaimed(lambda env, text=text: text)
        if changed and cascade:
            later = changed._bf_route_later_drafts() - changed
            if later:
                later._bf_route_sync_mileage(cascade=False)

    def _bf_route_later_drafts(self, employee=None, after=None):
        """The later route days of the same employees and years whose claim is still a draft.
        ``employee`` and ``after`` ((date, id)) name another employee and point in time: the
        one a day was taken from."""
        Expense = self.env["hr.expense"].sudo()
        later = self.browse()
        for day in self:
            who = employee or day._bf_route_employee()
            when = after or (day.date, day.id)
            if not who:
                continue
            drafts = Expense.search([
                ("employee_id", "=", who.id),
                ("bf_route_day_id", "!=", False),
                ("date", ">=", when[0]),
                ("date", "<=", date(when[0].year, 12, 31)),
                ("state", "=", "draft"),
                ("sheet_id", "=", False),
            ])
            later |= drafts.bf_route_day_id.filtered(lambda d, when=when: (d.date, d.id) > when)
        return later

    def _bf_route_sync_one(self, keep_edits=False, alert=False):
        """True when the claim of the day changed. ``keep_edits``: a later day redone because
        an earlier one changed; a draft whose kilometres the worker changed is left alone."""
        self.ensure_one()
        claimed = self.expense_ids
        reason, in_settings = self._bf_route_no_claim_reason()
        if reason:
            if self.vehicle_id.bf_route_personal and self.distance_actual_km > 0:
                # Own car and kilometres, yet nothing claimed: the office must know why.
                self._bf_route_note(reason)
                if alert:
                    self._bf_route_alert_unclaimed(
                        lambda env: self.with_env(env)._bf_route_no_claim_reason()[0])
            # A setting fixed later would not bring a deleted draft back; but a draft of someone
            # who no longer drives this day is a fact of the day, it goes.
            if in_settings and not (claimed.employee_id - self._bf_route_employee()):
                return False
            lines = []
        else:
            employee = self._bf_route_employee()
            lines = self._bf_route_mileage_lines(employee)
        who = employee.id if lines else False
        if sorted((e.product_id.id, round(e.quantity, 1), e.date, e.employee_id.id) for e in claimed) == \
                sorted((p.id, km, self.date, who) for p, km in lines):
            return False
        if keep_edits and claimed and round(sum(claimed.mapped("quantity")), 1) != round(
                self.distance_actual_km, 1):
            self._bf_route_note(_(
                "The threshold moved, but the expense of this day was changed by hand: %(expenses)s. "
                "It was not redone; check its rate.", expenses=", ".join(claimed.mapped("name"))))
            return False
        if claimed.filtered(lambda e: e.state != "draft" or e.sheet_id):
            self._bf_route_note(_(
                "The kilometres changed after the expense was reported: %(expenses)s. Check it "
                "with the person who approves it; it was not changed.",
                expenses=", ".join(claimed.mapped("name"))))
            return False
        claimed.unlink()
        if lines:
            self._bf_route_create_expenses(employee, lines)
        return True

    def _bf_route_alert_unclaimed(self, why):
        """One activity for the person responsible, in their language (bf_route's _alert)."""
        self._alert(lambda env: env._("Kilometres not claimed: %s", self.name), why)

    def _bf_route_note(self, text):
        """A note on the day, once: the same note is not posted twice in a row."""
        self.ensure_one()
        body = Markup("<p>%s</p>") % text
        last = self.message_ids[:1]
        if last and last.body == body:
            return
        self.message_post(body=body)

    def _bf_route_create_expenses(self, employee, lines):
        self.ensure_one()
        user = self.user_id
        env = self.with_context(lang=user.lang or self.env.lang).env
        day_text = format_date(env, self.date)
        purpose = self.visited or env._("no stop done")
        Expense = env["hr.expense"].sudo().with_company(self.company_id)
        full = self._bf_route_mileage_products()[0]
        made = Expense.browse()
        for product, km in lines:
            if product == full:
                name = env._("Mileage: %(route)s, %(date)s", route=self.route_id.name, date=day_text)
            else:
                name = env._("Mileage beyond the yearly threshold: %(route)s, %(date)s",
                             route=self.route_id.name, date=day_text)
            made |= Expense.create({
                "name": name,
                "employee_id": employee.id,
                "product_id": product.id,
                "quantity": km,
                "date": self.date,
                "company_id": self.company_id.id,
                "payment_mode": "own_account",
                "bf_route_day_id": self.id,
                "description": env._(
                    "Odometer %(start)s to %(end)s. Customers visited: %(purpose)s.",
                    start=formatLang(env, self.odometer_start, digits=0),
                    end=formatLang(env, self.odometer_end, digits=0), purpose=purpose),
            })
        total_km = formatLang(self.env, sum(km for _p, km in lines), digits=1)
        self.message_post(body=Markup("<p>%s</p>") % _(
            "%(km)s km claimed: draft expense for %(who)s (%(amount)s).",
            km=total_km, who=employee.name,
            amount=formatLang(self.env, sum(made.mapped("total_amount_currency")),
                              currency_obj=self.company_id.currency_id)))
        return made
