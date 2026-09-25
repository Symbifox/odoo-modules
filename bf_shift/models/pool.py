from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .agreement import OFFER_METHODS


class BfShiftPool(models.Model):
    """Call list: who is offered extra hours or open shifts, and in what order.

    The list is posted: every employee can read it, with its counters, as
    the agreements require.
    """

    _name = "bf.shift.pool"
    _description = "Call list"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    agreement_id = fields.Many2one("bf.shift.agreement", required=True, tracking=True)
    job_id = fields.Many2one("hr.job", string="Position")
    department_id = fields.Many2one("hr.department")
    method = fields.Selection(OFFER_METHODS, string="Order of offers",
                              help="Empty: the order set in the working conditions.")
    member_ids = fields.One2many("bf.shift.pool.member", "pool_id", string="Members")
    counters_since = fields.Date("Counters since", default=fields.Date.context_today,
                                 tracking=True)
    company_id = fields.Many2one(related="agreement_id.company_id", store=True)

    def _method(self):
        self.ensure_one()
        return self.method or self.agreement_id.offer_method or "seniority"

    def action_reset_counters(self):
        for pool in self:
            members = pool.with_context(active_test=False).member_ids
            members.write({"hours_offered": 0.0, "hours_accepted": 0.0, "refusal_count": 0})
            pool.counters_since = fields.Date.context_today(self)
            pool.message_post(body=_("Counters reset."))
        return True


class BfShiftPoolMember(models.Model):
    _name = "bf.shift.pool.member"
    _description = "Call list member"
    _order = "pool_id, sequence, seniority_date, id"

    pool_id = fields.Many2one("bf.shift.pool", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="pool_id.company_id", store=True)
    employee_id = fields.Many2one("hr.employee", required=True)
    employee_user_id = fields.Many2one("res.users", compute="_compute_user", store=True,
                                       compute_sudo=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    seniority_date = fields.Date(compute="_compute_seniority", store=True, compute_sudo=True)
    registered_on = fields.Date(default=fields.Date.context_today)
    withdrawn_on = fields.Date()
    withdraw_reason = fields.Selection([("own", "At the employee's request"),
                                        ("refusals", "Too many refusals"),
                                        ("other", "Other")])
    hours_offered = fields.Float(help="Hours offered since the counters were reset, "
                                 "refusals included when the agreement says so.")
    hours_accepted = fields.Float()
    refusal_count = fields.Integer("Refusals")

    _sql_constraints = [
        ("employee_unique", "unique(pool_id, employee_id)", "A person is on a list once."),
    ]

    @api.depends("employee_id.user_id")
    def _compute_user(self):
        for rec in self:
            rec.employee_user_id = rec.employee_id.user_id

    @api.depends("employee_id.shift_seniority_date")
    def _compute_seniority(self):
        for rec in self:
            rec.seniority_date = rec.employee_id.shift_seniority_date

    def action_withdraw(self):
        for rec in self:
            # The person themselves, or a manager: _withdraw() writes as superuser.
            self.env["bf.shift.assignment"]._check_is_employee_or_manager(rec.employee_id)
            rec._withdraw("own")
        return True

    def _withdraw(self, reason):
        self.ensure_one()
        self.sudo().write({"active": False, "withdrawn_on": fields.Date.context_today(self),
                           "withdraw_reason": reason})
        self.pool_id.sudo().message_post(body=_(
            "%(name)s is removed from the list (%(reason)s).",
            name=self.employee_id.name,
            reason=dict(self._fields["withdraw_reason"]._description_selection(self.env))[reason]))

    def action_reinstate(self):
        for rec in self:
            if rec.active:
                raise UserError(_("%(name)s is already on the list.", name=rec.employee_id.name))
            rec.write({"active": True, "withdrawn_on": False, "withdraw_reason": False,
                       "registered_on": fields.Date.context_today(self)})
            rec.pool_id.message_post(body=_("%(name)s is back on the list.",
                                            name=rec.employee_id.name))
        return True
