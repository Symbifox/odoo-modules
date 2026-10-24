from dateutil.relativedelta import relativedelta
from freezegun import freeze_time

from odoo.addons.bf_school_childcare.tests.common import ChildcareFixture


class BillingFixture(ChildcareFixture):
    """The child-care school of bf_school_childcare, with its prices.

    The office (Administration alone) bills, without the Invoicing group, as a school sets it up.
    """

    @classmethod
    def _setup_billing(cls):
        cls._setup_childcare()
        env = cls.env
        Rate = env["bf.school.childcare.rate"]
        base = {"school_id": cls.school.id, "year_id": cls.year.id}
        cls.r_slot = Rate.create(dict(base, kind="slot", amount=12.0))
        cls.r_full = Rate.create(dict(base, kind="full_day", amount=47.0, rl24_cap=22.0))
        cls.r_notice = Rate.create(dict(base, kind="late_notice", amount=3.0))
        cls.r_late = Rate.create(dict(base, kind="late_pickup", amount=10.0, block_minutes=15,
                                      next_block_minutes=15, next_block_amount=5.0))
        cls.r_walk = Rate.create(dict(base, kind="walk_in", amount=18.0))
        cls.tier_low = env["bf.school.childcare.tier"].create(dict(base, min_periods=1, max_periods=2, amount=100.0))
        cls.tier_high = env["bf.school.childcare.tier"].create(dict(base, min_periods=3, max_periods=10,
                                                                    amount=250.0))
        # The month of the Monday (and of the days after it, when the week spans two months) is
        # billed on the 2nd of the month after.
        cls.month = cls.monday.replace(day=1)
        cls.months = sorted({day.replace(day=1) for day in (cls.monday, cls.pedagogical, cls.holiday)})
        cls.bill_day = cls.months[-1] + relativedelta(months=1, day=2)

    def _run(self, month=None, user=None):
        Run = self.env["bf.school.childcare.billing.run"].with_user(user or self.office)
        month = month or self.month
        return Run.search([("school_id", "=", self.school.id), ("month", "=", month)]) or Run.create(
            {"school_id": self.school.id, "month": month})

    def _bill(self, user=None, day=None):
        """Bill every month of the week tested; returns the run of the Monday's month."""
        with freeze_time(self._at(day or self.bill_day, 9.0)):
            for month in reversed(self.months):
                run = self._run(month, user)
                run.action_bill()
        return run

    def _charges(self, student=None, **domain):
        dom = [("school_id", "=", self.school.id)] + [(k, "=", v) for k, v in domain.items()]
        if student:
            dom.append(("student_id", "=", student.id))
        return self.env["bf.school.childcare.charge"].search(dom)

    def _invoices(self, partner):
        return self.env["account.move"].search([("partner_id", "=", partner.id), ("move_type", "=", "out_invoice"),
                                                ("school_childcare_run_id.school_id", "=", self.school.id)])

    def _pay(self, move, amount=None):
        self.env["account.payment.register"].with_context(active_model="account.move", active_ids=move.ids).create(
            {"amount": amount or move.amount_residual})._create_payments()

    def _attend(self, student, slot=None, day=None, arrive=15.6, depart=None, partner=None):
        """A child booked or registered comes, and leaves at `depart` with `partner`."""
        sheet = self.env["bf.school.childcare.sheet"].search([("slot_id", "=", (slot or self.after).id),
                                                              ("date", "=", day or self.monday)]) \
            or self._sheet(slot=slot, day=day)
        line = self._line(sheet, student).with_user(self.educator)
        with freeze_time(self._at(sheet.date, arrive)):
            line.action_arrive()
        if depart:
            with freeze_time(self._at(sheet.date, depart)):
                line._school_depart("guardian", partner=(partner or self.mom).partner_id)
        return line
