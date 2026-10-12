"""Time sheets from route days: one line per finished day, start to finish, on the route's project.

⚠️ Logged when the worker finishes the day on the phone, in sudo, for the worker's own
employee only, from a clean context (``clean_env``), and never blocks the end of the day: a
line that fails leaves a note on the day for the office.
⚠️ The phone's clock sets the start and the end (a mark made without network keeps its
time). Not logged, a note instead, the office enters the time by hand: a day longer than
MAX_HOURS (left open), or a start the server received more than LATE_START after the time
the phone gave it (a clock set back, or a past day started after the fact over RPC). A
late end cannot lengthen the day: the phone's time is not trusted past the server's (a few
minutes of drift are tolerated).
"""
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import formatLang

from odoo.addons.bf_route.models.bf_route_day import clean_env

_logger = logging.getLogger(__name__)

# Longer than this, the day was left open (or its start forged): not logged, a note says so.
MAX_HOURS = 14
# A start received this long after the phone's time of it is not trusted for the hours.
LATE_START = timedelta(minutes=15)


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_route_timesheet_project_id = fields.Many2one(
        "project.project", string="Route time sheets",
        help="Project of the time of the routes that name none. Empty: no time is logged.")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_route_timesheet_project_id = fields.Many2one(
        related="company_id.bf_route_timesheet_project_id", readonly=False,
        domain="[('allow_timesheets', '=', True), ('company_id', 'in', (company_id, False))]")


class BfRoute(models.Model):
    _inherit = "bf.route"

    # ⚠️ Groups on the fields, not only in the view: the lines are made in sudo on this project.
    timesheet_project_id = fields.Many2one(
        "project.project", string="Project", check_company=True, groups="project.group_project_user",
        copy=False,
        domain="[('allow_timesheets', '=', True)]",
        help="Where the time of each finished day goes. Empty: the default project of the settings.")
    timesheet_task_id = fields.Many2one(
        "project.task", string="Task", check_company=True, groups="project.group_project_user",
        copy=False,
        domain="[('project_id', '=', timesheet_project_id)]")

    @api.constrains("timesheet_project_id", "timesheet_task_id")
    def _check_timesheet_task(self):
        for route in self.sudo():
            if route.timesheet_task_id and route.timesheet_task_id.project_id != route.timesheet_project_id:
                raise ValidationError(_("The task must belong to the route's project."))
            if route.timesheet_project_id and not route.timesheet_project_id.allow_timesheets:
                raise ValidationError(_("The route's project must accept time sheets."))

    @api.onchange("timesheet_project_id")
    def _onchange_timesheet_project(self):
        if self.timesheet_task_id.project_id != self.timesheet_project_id:
            self.timesheet_task_id = False


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    bf_route_day_id = fields.Many2one(
        "bf.route.day", string="Route day", readonly=True, copy=False, index="btree_not_null",
        ondelete="set null")

    # ⚠️ An employee may write their own time sheets over RPC: the link to a route day is
    # set by the route module only (sudo).
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(vals.get("bf_route_day_id") for vals in vals_list):
            raise AccessError(_("Only the route module links a time sheet to a route day."))
        return super().create(vals_list)

    def write(self, vals):
        if "bf_route_day_id" in vals and not self.env.su:
            raise AccessError(_("Only the route module links a time sheet to a route day."))
        return super().write(vals)


class BfRouteDay(models.Model):
    _inherit = "bf.route.day"

    timesheet_ids = fields.One2many("account.analytic.line", "bf_route_day_id",
                                    string="Time sheets", readonly=True)
    timesheet_hours = fields.Float(string="Time logged", compute="_compute_timesheet_hours")
    start_received_at = fields.Datetime(
        string="Start received", readonly=True, copy=False,
        help="When the server received the start of the day, whatever time the phone gave it.")

    def _compute_timesheet_hours(self):
        for day in self:
            day.timesheet_hours = sum(day.sudo().timesheet_ids.mapped("unit_amount"))

    def action_start(self, odometer=None, safety_check=False, client_time=None):
        result = super().action_start(odometer=odometer, safety_check=safety_check, client_time=client_time)
        self.sudo().with_context(bf_route_auto=True).write({"start_received_at": fields.Datetime.now()})
        return result

    def action_finish(self, odometer=None, client_time=None):
        result = super().action_finish(odometer=odometer, client_time=client_time)
        self._bf_route_log_time()
        return result

    def action_open_timesheets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Time sheets"),
            "res_model": "account.analytic.line",
            "view_mode": "list,form",
            "domain": [("bf_route_day_id", "=", self.id)],
            "context": {"create": False},
        }

    def _bf_route_time_project(self):
        self.ensure_one()
        route = self.route_id.sudo()
        if route.timesheet_project_id:
            return route.timesheet_project_id, route.timesheet_task_id
        return self.company_id.sudo().bf_route_timesheet_project_id, self.env["project.task"]

    def _bf_route_log_time(self):
        """One time sheet line per finished day that has none. Never raises."""
        for day in clean_env(self).sudo():
            if day.state != "done" or day.timesheet_ids:
                continue
            project, task = day._bf_route_time_project()
            if not project or not (day.date_started and day.date_finished):
                continue
            try:
                with self.env.cr.savepoint():
                    day._bf_route_log_one(project, task)
            except (UserError, ValidationError, AccessError) as exc:
                _logger.info("bf_route_timesheet: day %s not logged: %s", day.id, exc)
                text = exc.args[0] if exc.args else str(exc)
                day.message_post(body=Markup("<p>%s</p>") % _("The time of the day was not logged: %s", text))
                # Nobody reads a note on a day that is over: one activity for the person responsible.
                day._alert(lambda env, day=day: env._("Time not logged: %s", day.name),
                           lambda env, text=text: text)

    def _bf_route_log_one(self, project, task):
        self.ensure_one()
        employee = self.user_id.sudo().employee_ids.filtered(
            lambda e: e.company_id == self.company_id and e.active)[:1]
        if not employee:
            raise UserError(_("%s has no employee in this company.", self.user_id.name or "-"))
        hours = round((self.date_finished - self.date_started).total_seconds() / 3600.0, 2)
        if hours <= 0:
            return
        if hours > MAX_HOURS:
            raise UserError(_("%(hours)s h between the start and the end of the day is more than "
                              "%(max)s h: enter the time by hand.",
                              hours=formatLang(self.env, hours, digits=1), max=MAX_HOURS))
        if not self.start_received_at or self.start_received_at - self.date_started > LATE_START:
            raise UserError(_("The start of the day reached the server %(minutes)s min after the time "
                              "the phone gave it: enter the time by hand.",
                              minutes=round((self.start_received_at - self.date_started).total_seconds() / 60)
                              if self.start_received_at else "?"))
        env = self.with_context(lang=self.user_id.lang or self.env.lang).env
        done = len(self.stop_ids.filtered(lambda s: s.state == "done"))
        env["account.analytic.line"].sudo().with_company(self.company_id).create({
            "name": env._("Route %(route)s: %(done)s of %(total)s stops done",
                          route=self.route_id.name, done=done, total=len(self.stop_ids)),
            "date": self.date,
            "employee_id": employee.id,
            "project_id": project.id,
            "task_id": task.id or False,
            "unit_amount": hours,
            "bf_route_day_id": self.id,
        })
        self.message_post(body=Markup("<p>%s</p>") % _(
            "%(hours)s h logged for %(who)s on %(project)s.",
            hours=formatLang(self.env, hours, digits=2), who=employee.name,
            project=project.display_name))
