from datetime import timedelta

from odoo import _, fields, models

WAITING_STATES = ("05_waiting_client", "06_waiting_external")
CLOSED_STATES = ("1_done", "1_canceled")
# Une échéance ou une activité prévue dans cet horizon tient lieu de suite.
FOLLOW_UP_DAYS = 30


class BfIrregularityDetector(models.Model):
    _inherit = "bf.irregularity.detector"

    def _task_owner(self, task):
        return task.user_ids.filtered("active")[:1] or task.project_id.user_id or None

    def _detect_task_waiting_too_long(self):
        Task = self.env["project.task"].sudo()
        tasks = Task.search([("state", "in", WAITING_STATES)])
        if not tasks:
            return []
        # La dernière valeur suivie de « state » date l'entrée en attente :
        # la tâche y est encore, donc aucun changement d'état depuis.
        field = self.env["ir.model.fields"]._get("project.task", "state")
        self.env.cr.execute("""
            SELECT m.res_id, MAX(m.date)
              FROM mail_tracking_value v
              JOIN mail_message m ON m.id = v.mail_message_id
             WHERE v.field_id = %s AND m.model = 'project.task' AND m.res_id IN %s
          GROUP BY m.res_id
        """, (field.id, tuple(tasks.ids)))
        entered = dict(self.env.cr.fetchall())
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        horizon = today + timedelta(days=FOLLOW_UP_DAYS)
        # Une échéance ou une activité déjà passée ne compte pas : la suite prévue n'a pas eu lieu.
        planned = set(self.env["mail.activity"].sudo().search([
            ("res_model", "=", "project.task"),
            ("res_id", "in", tasks.ids),
            ("date_deadline", ">=", today),
            ("date_deadline", "<=", horizon),
        ]).mapped("res_id"))
        threshold = self.threshold or 21
        labels = dict(Task._fields["state"]._description_selection(self.env))
        detections = []
        for task in tasks:
            since = entered.get(task.id) or task.create_date
            days = (now - since).days
            if days < threshold or task.id in planned:
                continue
            if task.date_deadline and today <= task.date_deadline.date() <= horizon:
                continue
            detections.append({
                "record": task,
                "owner": self._task_owner(task),
                "severity": "critical" if days >= 2 * threshold else None,
                "message": _("“%(state)s” for %(days)s days, with no follow-up", state=labels.get(task.state, task.state), days=days),
            })
        return detections

    def _detect_task_unassigned(self):
        tasks = self.env["project.task"].sudo().search([
            ("state", "not in", CLOSED_STATES),
            ("user_ids", "=", False),
            ("project_id", "!=", False),
            ("project_id.active", "=", True),
        ])
        return [{
            "record": task,
            "owner": task.project_id.user_id or None,
            "message": _("Open task with nobody assigned (project %s)", task.project_id.name),
        } for task in tasks]

    def _detect_task_in_archived_project(self):
        projects = self.env["project.project"].sudo().with_context(active_test=False).search([("active", "=", False)])
        tasks = self.env["project.task"].sudo().search([
            ("state", "not in", CLOSED_STATES),
            ("project_id", "in", projects.ids),
        ])
        return [{
            "record": task,
            "owner": self._task_owner(task),
            "message": _("Open task in the archived project %s", task.project_id.name),
        } for task in tasks]

    def _days_off(self, employee, start, end):
        """Jours couverts par un congé approuvé, entre deux dates incluses.

        ``hr_timesheet`` ne dépend pas de ``hr_holidays`` : sans lui, l'ensemble
        est vide et seul le calendrier de travail écarte les jours non ouvrés.
        Sans cet écart, une semaine de vacances approuvée rendait cinq constats
        « aucun temps saisi ».
        """
        if "hr.leave" not in self.env:
            return set()
        leaves = self.env["hr.leave"].sudo().search([
            ("employee_id", "=", employee.id),
            ("state", "=", "validate"),
            ("date_from", "<=", "%s 23:59:59" % end),
            ("date_to", ">=", "%s 00:00:00" % start),
        ])
        off = set()
        for leave in leaves:
            day, last = leave.date_from.date(), leave.date_to.date()
            while day <= last:
                off.add(day)
                day += timedelta(days=1)
        return off

    def _detect_timesheet_missing_day(self):
        """Jours ouvrés sans aucune saisie de temps sur un projet.

        Ne balaie qu'une fenêtre de jours : un jour sorti de la fenêtre n'est
        plus vérifié, donc son irrégularité reste ouverte au lieu de se
        « résoudre » d'elle-même.
        """
        today = fields.Date.context_today(self)
        window = self.threshold or 5
        employees = self.env["hr.employee"].sudo().search([("user_id", "!=", False), ("user_id.active", "=", True)])
        detections, checked = [], set()
        for emp in employees:
            calendar = emp.resource_calendar_id
            start = fields.Date.to_date(emp.create_date)
            days, day = [], today - timedelta(days=1)
            while len(days) < window and day >= start:
                works = calendar._works_on_date(day) if calendar else day.weekday() < 5
                if works:
                    days.append(day)
                day -= timedelta(days=1)
            if not days:
                continue
            logged = set(self.env["account.analytic.line"].sudo().search([
                ("employee_id", "=", emp.id),
                ("project_id", "!=", False),
                ("date", ">=", min(days)),
                ("date", "<=", max(days)),
            ]).mapped("date"))
            off = self._days_off(emp, min(days), max(days))
            for day in days:
                key = "hr.employee,%s,%s" % (emp.id, day)
                checked.add(key)
                if day in logged or day in off:
                    continue
                detections.append({
                    "record": emp,
                    "key": key,
                    "owner": emp.user_id,
                    "message": _("No time logged on %s", fields.Date.to_string(day)),
                })
        return detections, checked
