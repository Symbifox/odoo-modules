import math
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# A stop dialog opened this recently is still being handled somewhere.
CLAIM_MINUTES = 5


class BfTimer(models.Model):
    _name = "bf.timer"
    _description = "Timer de feuille de temps"
    _order = "start_time desc"

    user_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.uid, index=True,
    )
    employee_id = fields.Many2one("hr.employee", required=True)
    project_id = fields.Many2one("project.project", required=True)
    task_id = fields.Many2one(
        "project.task", required=True,
        domain="[('project_id', '=', project_id), ('allow_timesheets', '=', True)]",
    )
    start_time = fields.Datetime(required=True, default=fields.Datetime.now)
    # ⚠️ start_time moves on every Resume and Cancel. first_start never moves,
    # and it is what dates the timesheet (see _timesheet_date). A dedicated
    # field rather than create_date: create_date is the database transaction
    # clock, not the clock start_time is written with, and it is rewritten by
    # any import or duplication of the record.
    first_start = fields.Datetime(
        string="First start", default=fields.Datetime.now, copy=False, readonly=True,
        help="When the timer was first started. Resume and Cancel move the start "
             "time; this does not, and it dates the timesheet.",
    )
    is_active = fields.Boolean(default=True, index=True)
    description = fields.Char()
    claimed_at = fields.Datetime(
        help="Set when a stop wizard claims this timer. Prevents other browser windows from showing the dialog.",
    )
    is_paused = fields.Boolean(default=False, index=True)
    accumulated_seconds = fields.Float(default=0)

    # -------------------------------------------------------------------------
    # RPC methods called from JS
    # -------------------------------------------------------------------------

    @api.model
    def get_active_timers(self):
        """Return active timers for the current user."""
        timers = self.search([
            ("user_id", "=", self.env.uid),
            ("is_active", "=", True),
        ])
        now = fields.Datetime.now()
        result = []
        for t in timers:
            if t.is_paused:
                elapsed = t.accumulated_seconds
            else:
                elapsed = t.accumulated_seconds + (now - t.start_time).total_seconds()
            result.append({
                "id": t.id,
                "project_name": self._project_label(t.project_id)[0],
                "task_name": t.task_id.name,
                "task_id": t.task_id.id,
                "project_id": t.project_id.id,
                "start_time_iso": fields.Datetime.to_string(t.start_time),
                "elapsed_seconds": max(0, elapsed),
                "description": t.description or t.task_id.name,
                "is_paused": t.is_paused,
                "accumulated_seconds": t.accumulated_seconds,
            })
        return result

    @api.model
    def get_recent_tasks(self, limit=5):
        """Return the top N tasks by most recent timesheet entries.

        Pinned tasks are returned first, then recent tasks, deduplicated.
        """
        today = fields.Date.context_today(self)
        # Fetch pinned task IDs for current user
        pinned_recs = self.env["bf.timer.pinned.task"].search(
            [("user_id", "=", self.env.uid)], order="sequence, id",
        )
        pinned_task_ids = pinned_recs.mapped("task_id").ids
        # ⚠️ A pin whose task the user can no longer read (moved to a private
        # project, say) is skipped, not raised: reading its name below raised
        # AccessError and took down the systray, the page and the phone app
        # with it. `search` applies the record rules in SQL, so a value already
        # in cache cannot let it through.
        # ⚠️ Read across ALL the user's companies, not the ones selected in the
        # switcher: a pin is personal, and a task of company B pinned while A
        # and B were selected must not vanish when only A is.
        Task = self._all_user_companies().env["project.task"]
        if pinned_task_ids:
            readable = set(Task.search([("id", "in", pinned_task_ids)]).ids)
            pinned_task_ids = [tid for tid in pinned_task_ids if tid in readable]

        self.env.cr.execute("""
            SELECT sub.task_id, sub.task_name, sub.project_id, sub.project_name,
                   sub.project_color, sub.stage_name, sub.last_date, sub.state
              FROM (
                SELECT aal.task_id,
                       pt.name AS task_name,
                       aal.project_id,
                       pp.name AS project_name,
                       pp.color AS project_color,
                       ptt.name AS stage_name,
                       pt.state AS state,
                       MAX(aal.date) AS last_date
                  FROM account_analytic_line aal
                  JOIN project_task pt ON pt.id = aal.task_id
                  JOIN project_project pp ON pp.id = aal.project_id
             LEFT JOIN project_task_type ptt ON ptt.id = pt.stage_id
                 WHERE aal.user_id = %s
                   AND aal.task_id IS NOT NULL
                   AND pt.active = true
                   AND pp.allow_timesheets = true
                 GROUP BY aal.task_id, pt.name, aal.project_id, pp.name,
                          pp.color, ptt.name, pt.state
                 ORDER BY last_date DESC
                 LIMIT %s
              ) sub
        """, (self.env.uid, limit))
        rows = self.env.cr.dictfetchall()
        # task_name / project_name / stage_name are JSONB in Odoo 18
        lang = self.env.lang or "en_US"

        def _resolve_jsonb(val):
            if isinstance(val, dict):
                return val.get(lang) or val.get("en_US") or next(iter(val.values()), "")
            return val

        def _build_entry(r, is_pinned):
            tname = _resolve_jsonb(r["task_name"])
            pname = _resolve_jsonb(r["project_name"])
            sname = _resolve_jsonb(r["stage_name"])
            last_date = r["last_date"]
            delta_days = (today - last_date).days if last_date else None
            if delta_days is not None:
                if delta_days == 0:
                    date_label = "aujourd'hui"
                elif delta_days == 1:
                    date_label = "hier"
                else:
                    date_label = f"il y a {delta_days}j"
            else:
                date_label = ""
            state = r.get("state", "")
            return {
                "task_id": r["task_id"],
                "task_name": tname,
                "project_id": r["project_id"],
                "project_name": pname,
                "project_color": r["project_color"] or 0,
                "stage_name": sname or "",
                "date_label": date_label,
                "is_closed": state in ("1_done", "1_canceled"),
                "is_pinned": is_pinned,
            }

        # Build result: pinned tasks first, then recent (deduplicated)
        seen_ids = set()
        result = []
        rows_by_task = {r["task_id"]: r for r in rows}
        # Pinned tasks first (in pinned order)
        for tid in pinned_task_ids:
            if tid in rows_by_task:
                result.append(_build_entry(rows_by_task[tid], True))
                seen_ids.add(tid)
            else:
                # Pinned task not in recent timesheets — fetch directly
                task = Task.browse(tid)
                if task.exists() and task.active:
                    project_name, project_color = self._project_label(task.project_id)
                    result.append({
                        "task_id": task.id,
                        "task_name": task.name,
                        "project_id": task.project_id.id,
                        "project_name": project_name,
                        "project_color": project_color,
                        "stage_name": task.stage_id.name or "",
                        "date_label": "",
                        "is_closed": task.state in ("1_done", "1_canceled"),
                        "is_pinned": True,
                    })
                    seen_ids.add(tid)
        # Then recent tasks (not already pinned)
        for r in rows:
            if r["task_id"] not in seen_ids:
                result.append(_build_entry(r, False))
                seen_ids.add(r["task_id"])
        return result

    @api.model
    def start_timer(self, task_id):
        """Start a new timer for the given task."""
        task = self.env["project.task"].browse(task_id)
        if not task.exists():
            raise UserError("Tâche introuvable.")
        if not task.allow_timesheets:
            raise UserError("Les feuilles de temps ne sont pas activées sur cette tâche.")
        existing = self.search([
            ("user_id", "=", self.env.uid),
            ("task_id", "=", task_id),
            ("is_active", "=", True),
        ], limit=1)
        if existing:
            raise UserError("Un timer est déjà en cours pour cette tâche.")
        # ⚠️ The employee of the TASK's company first, as hr_timesheet picks it:
        # a person employed by two companies times a task of B as employee B,
        # whatever company is current. Read in sudo (plain users cannot read
        # hr.employee), with a domain that names the user.
        Employee = self.env["hr.employee"].sudo()
        employee = Employee.browse()
        if task.company_id:
            employee = Employee.search([
                ("user_id", "=", self.env.uid),
                ("company_id", "=", task.company_id.id),
            ], limit=1)
        if not employee:
            employee = self.env.user.employee_id or Employee.search(
                [("user_id", "=", self.env.uid), ("company_id", "in", self.env.companies.ids)],
                limit=1,
            )
        if not employee:
            raise UserError("Aucun employé associé à votre compte utilisateur.")
        now = fields.Datetime.now()
        timer = self.create({
            "user_id": self.env.uid,
            "employee_id": employee.id,
            "project_id": task.project_id.id,
            "task_id": task.id,
            "start_time": now,
            "first_start": now,
            "is_active": True,
            "description": task.name,
        })
        return {
            "id": timer.id,
            "project_name": self._project_label(timer.project_id)[0],
            "task_name": timer.task_id.name,
            "task_id": timer.task_id.id,
            "project_id": timer.project_id.id,
            "start_time_iso": fields.Datetime.to_string(timer.start_time),
            "elapsed_seconds": 0,
            "description": timer.description,
            "is_paused": False,
            "accumulated_seconds": 0,
        }

    @api.model
    def stop_timer(self, timer_id):
        """Stop a timer and return data for the confirmation dialog."""
        timer = self.browse(timer_id)
        if not timer.exists() or timer.user_id.id != self.env.uid:
            raise UserError("Timer introuvable.")
        elapsed = timer._stop_and_freeze()
        suggested_minutes = self._compute_suggested_minutes(elapsed)
        suggested_hours = round(suggested_minutes / 60.0, 4)
        rounding = self.get_rounding_settings()
        return {
            "timer_id": timer.id,
            "task_name": timer.task_id.name,
            "task_id": timer.task_id.id,
            "project_name": self._project_label(timer.project_id)[0],
            "project_id": timer.project_id.id,
            "elapsed_seconds": max(0, elapsed),
            "suggested_hours": suggested_hours,
            "suggested_minutes": suggested_minutes,
            "description": timer.description or timer.task_id.name,
            "rounding_increment": rounding["increment"],
            "rounding_mode": rounding["mode"],
        }

    @api.model
    def confirm_timesheet(self, timer_id, duration_hours, description):
        """Create the timesheet entry from a stopped timer."""
        timer = self.browse(timer_id)
        if not timer.exists() or timer.user_id.id != self.env.uid:
            raise UserError("Timer introuvable.")
        if duration_hours <= 0:
            raise ValidationError("La durée doit être supérieure à 0.")
        self.env["account.analytic.line"].create({
            "name": description or timer.task_id.name,
            "date": timer._timesheet_date(),
            "unit_amount": duration_hours,
            "task_id": timer.task_id.id,
            "project_id": timer.project_id.id,
            "employee_id": timer.employee_id.id,
        })
        timer.unlink()
        return True

    @api.model
    def get_pending_timers(self):
        """Return timers that were stopped but not yet confirmed/discarded.

        ⚠️ 1.12.0 — the elapsed time is the one FROZEN at stop
        (``accumulated_seconds``), never recomputed from ``start_time``: a timer
        stopped at 10:00 and confirmed at 14:00 used to propose four hours too
        many, and a timer paused before the stop counted twice.

        ⚠️ Claimed timers are no longer hidden. ``claimed`` is True when a stop
        dialog was opened in the last ``CLAIM_MINUTES`` minutes: the web client
        skips those when it auto-opens dialogs, so one stop still opens one
        dialog, but a caller that shows no dialog (the phone) still sees the
        timer instead of losing it for five minutes.
        """
        timers = self.search([
            ("user_id", "=", self.env.uid),
            ("is_active", "=", False),
        ])
        cutoff = fields.Datetime.now() - timedelta(minutes=CLAIM_MINUTES)
        rounding = self.get_rounding_settings()
        result = []
        for t in timers:
            elapsed = t._elapsed_seconds()
            suggested_minutes = self._compute_suggested_minutes(elapsed)
            result.append({
                "timer_id": t.id,
                "task_name": t.task_id.name,
                "task_id": t.task_id.id,
                "project_name": self._project_label(t.project_id)[0],
                "project_id": t.project_id.id,
                "elapsed_seconds": elapsed,
                "suggested_hours": round(suggested_minutes / 60.0, 4),
                "suggested_minutes": suggested_minutes,
                "description": t.description or t.task_id.name,
                "rounding_increment": rounding["increment"],
                "rounding_mode": rounding["mode"],
                "claimed": bool(t.claimed_at and t.claimed_at >= cutoff),
            })
        return result

    @api.model
    def reactivate_timer(self, timer_id):
        """Re-activate a timer that was stopped (Cancel in dialog)."""
        timer = self.browse(timer_id)
        if not timer.exists() or timer.user_id.id != self.env.uid:
            raise UserError("Timer introuvable.")
        if timer.is_active:
            # ⚠️ Already running (a second Cancel, another tab): nothing to do.
            # Restarting start_time here threw away the running segment.
            return True
        # ⚠️ start_time restarts now: the time before the stop is already
        # folded into accumulated_seconds. Keeping the old start_time would
        # count again everything, dialog time included.
        timer.write({
            "is_active": True,
            "claimed_at": False,
            "is_paused": False,
            "start_time": fields.Datetime.now(),
        })
        return True

    @api.model
    def discard_timer(self, timer_id):
        """Delete a timer without creating a timesheet."""
        timer = self.browse(timer_id)
        if not timer.exists() or timer.user_id.id != self.env.uid:
            raise UserError("Timer introuvable.")
        timer.unlink()
        return True

    @api.model
    def get_today_total(self):
        """Return total hours logged today by the current user."""
        today = fields.Date.context_today(self)
        self.env.cr.execute("""
            SELECT COALESCE(SUM(unit_amount), 0)
              FROM account_analytic_line
             WHERE user_id = %s AND date = %s AND project_id IS NOT NULL
        """, (self.env.uid, today))
        return self.env.cr.fetchone()[0]

    @api.model
    def get_week_total(self):
        """Return total hours logged this week (Monday-Sunday) by the current user."""
        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday())
        self.env.cr.execute("""
            SELECT COALESCE(SUM(unit_amount), 0)
              FROM account_analytic_line
             WHERE user_id = %s AND date >= %s AND date <= %s
               AND project_id IS NOT NULL
        """, (self.env.uid, monday, today))
        return self.env.cr.fetchone()[0]

    @api.model
    def get_description_presets(self):
        """Return active description presets for the stop dialog."""
        presets = self.env["bf.timer.description.preset"].search(
            [("active", "=", True)], order="sequence, id",
        )
        return [{"id": p.id, "name": p.name, "text": p.text} for p in presets]

    @api.model
    def pin_task(self, task_id):
        """Pin a task as favorite for the current user.

        ⚠️ Refused for a task the user cannot read: nothing checks the target
        of a many2one, so the pin would be created and then be unreadable.
        """
        task = self._all_user_companies().env["project.task"].browse(task_id).exists()
        if not task:
            raise UserError("Tâche introuvable.")
        # Across all the user's companies, like the filter of get_recent_tasks.
        task.check_access("read")
        PinnedTask = self.env["bf.timer.pinned.task"]
        existing = PinnedTask.search([
            ("user_id", "=", self.env.uid),
            ("task_id", "=", task_id),
        ], limit=1)
        if not existing:
            PinnedTask.create({"user_id": self.env.uid, "task_id": task_id})
        return True

    @api.model
    def unpin_task(self, task_id):
        """Unpin a task for the current user."""
        self.env["bf.timer.pinned.task"].search([
            ("user_id", "=", self.env.uid),
            ("task_id", "=", task_id),
        ]).unlink()
        return True


    @api.model
    def pause_timer(self, timer_id):
        """Pause a running timer, accumulating elapsed seconds."""
        timer = self.browse(timer_id)
        if not timer.exists() or timer.user_id.id != self.env.uid:
            raise UserError("Timer introuvable.")
        if not timer.is_active:
            # ⚠️ A stopped timer's time is frozen in accumulated_seconds; a
            # pause would add the whole segment since start_time again.
            raise UserError("Ce timer est arrêté : il ne se met pas en pause.")
        if timer.is_paused:
            raise UserError("Ce timer est d\u00e9j\u00e0 en pause.")
        now = fields.Datetime.now()
        segment = (now - timer.start_time).total_seconds()
        timer.write({
            "is_paused": True,
            "accumulated_seconds": timer.accumulated_seconds + max(0, segment),
        })
        return True

    @api.model
    def resume_timer(self, timer_id):
        """Resume a paused timer."""
        timer = self.browse(timer_id)
        if not timer.exists() or timer.user_id.id != self.env.uid:
            raise UserError("Timer introuvable.")
        if not timer.is_active:
            raise UserError("Ce timer est arrêté : Annuler le relance.")
        if not timer.is_paused:
            raise UserError("Ce timer n'est pas en pause.")
        timer.write({
            "is_paused": False,
            "start_time": fields.Datetime.now(),
        })
        return True

    # -------------------------------------------------------------------------
    # Elapsed time
    # -------------------------------------------------------------------------

    def _elapsed_seconds(self, now=None):
        """Elapsed seconds of ONE timer, whatever its state.

        Running: accumulated + current segment. Paused or stopped: the
        accumulated value alone, because the stop folds the last segment into
        it (see ``_stop_and_freeze``).
        """
        self.ensure_one()
        if not self.is_active or self.is_paused:
            return max(0.0, self.accumulated_seconds or 0.0)
        now = now or fields.Datetime.now()
        return max(0.0, (self.accumulated_seconds or 0.0)
                   + (now - self.start_time).total_seconds())

    def _timesheet_date(self):
        """The day the timesheet belongs to: the owner's day at FIRST start.

        🔴 In the timer owner's time zone, not UTC: a timer started at 22:30 in
        Montréal is 02:30 UTC the next day, and ``start_time.date()`` filed it
        there. UTC when the owner has no time zone. And at the first start, not
        at the last Resume or Cancel, which move ``start_time``.
        """
        self.ensure_one()
        moment = self.first_start or self.create_date or self.start_time
        return fields.Date.context_today(
            self.with_context(tz=self.user_id.tz or "UTC"), timestamp=moment)

    @api.model
    def _duration_hours(self, total_minutes):
        """Minutes confirmed on screen → hours written, as the stop dialog does.

        Same rule as ``bf_timer_stop_dialog.js`` and the stop wizard: below one
        rounding increment the increment is written (unless rounding is off),
        then two decimals. The browser dialog, the stop wizard and the phone app
        (``bf_timesheet_timer_mobile``) all write 25 minutes as 0.42 h.
        ⚠️ Not every writer goes through here: a caller that writes
        ``suggested_hours`` directly gets four decimals, not two.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        mode = ICP.get_param("bf_timer.rounding_mode", "round_all")
        increment = int(ICP.get_param("bf_timer.rounding_increment", "5"))
        if mode != "none" and total_minutes < increment:
            total_minutes = increment
        return round(total_minutes / 60.0, 2)

    @api.model
    def _project_label(self, project):
        """(name, colour) of a project, as a many2one shows it.

        ⚠️ A task can be readable in a project that is not (assigned in a
        private project): reading the project's name then raised AccessError
        and emptied the whole list. The NAME is read in sudo, exactly what Odoo
        does to display a many2one; nothing else of the project leaves, and the
        colour only when the project itself is readable.
        """
        if not project:
            return "", 0
        color = (project.color or 0) if project.has_access("read") else 0
        return project.sudo().name or "", color

    def _all_user_companies(self):
        """This recordset with every company of the user allowed, current first."""
        current = self.env.company.id
        ids = [current] + [c for c in self.env.user.company_ids.ids if c != current]
        return self.with_context(allowed_company_ids=ids)

    def _stop_and_freeze(self):
        """Stop the timer and freeze its elapsed time. Returns that time.

        🔴 The one place that stops a timer. ``stop_timer`` and the task form
        button used to each write ``is_active = False`` without folding the
        running segment into ``accumulated_seconds``; every later read then
        recomputed from ``start_time`` and the proposed duration kept growing
        after the stop.
        """
        self.ensure_one()
        if not self.is_active:
            # Already stopped (a double click, another tab): its time is frozen,
            # and stamping claimed_at again would re-hide it from other windows.
            return self._elapsed_seconds()
        now = fields.Datetime.now()
        elapsed = self._elapsed_seconds(now)
        self.write({
            "is_active": False,
            "is_paused": False,
            "claimed_at": now,
            "accumulated_seconds": elapsed,
        })
        return elapsed

    @api.model
    def get_rounding_settings(self):
        """Return rounding configuration for JS."""
        ICP = self.env["ir.config_parameter"].sudo()
        return {
            "mode": ICP.get_param("bf_timer.rounding_mode", "round_all"),
            "increment": int(ICP.get_param("bf_timer.rounding_increment", "5")),
            "threshold": int(ICP.get_param("bf_timer.rounding_threshold", "30")),
        }

    def _compute_suggested_minutes(self, elapsed_seconds):
        """Compute suggested minutes from raw elapsed seconds using rounding config."""
        ICP = self.env["ir.config_parameter"].sudo()
        mode = ICP.get_param("bf_timer.rounding_mode", "round_all")
        increment = int(ICP.get_param("bf_timer.rounding_increment", "5"))
        threshold = int(ICP.get_param("bf_timer.rounding_threshold", "30"))
        elapsed_minutes = elapsed_seconds / 60.0

        if mode == "none":
            return max(1, math.ceil(elapsed_minutes))

        should_round = (
            mode == "round_all"
            or (mode == "round_below_threshold" and elapsed_minutes < threshold)
        )
        if should_round:
            if elapsed_minutes < increment:
                return increment
            return math.ceil(elapsed_minutes / increment) * increment

        # Above threshold in round_below_threshold mode — just ceil to whole minute
        return max(1, math.ceil(elapsed_minutes))


class ProjectTask(models.Model):
    _inherit = "project.task"

    bf_has_active_timer = fields.Boolean(
        compute="_compute_bf_has_active_timer",
        string="Timer actif",
    )

    def _compute_bf_has_active_timer(self):
        active_timer_task_ids = set()
        if self.ids:
            self.env.cr.execute("""
                SELECT DISTINCT task_id FROM bf_timer
                 WHERE user_id = %s AND is_active = true AND task_id = ANY(%s)
            """, (self.env.uid, list(self.ids)))
            active_timer_task_ids = {r[0] for r in self.env.cr.fetchall()}
        for task in self:
            task.bf_has_active_timer = task.id in active_timer_task_ids

    def action_bf_start_timer(self):
        """Start a timer for this task (called from form button)."""
        self.ensure_one()
        return self.env["bf.timer"].start_timer(self.id)

    def action_bf_stop_timer(self):
        """Stop the active timer and open a wizard dialog instantly."""
        self.ensure_one()
        timer = self.env["bf.timer"].search([
            ("user_id", "=", self.env.uid),
            ("task_id", "=", self.id),
            ("is_active", "=", True),
        ], limit=1)
        if not timer:
            raise UserError("Aucun timer actif pour cette tâche.")
        elapsed = timer._stop_and_freeze()
        BfTimer = self.env["bf.timer"]
        suggested_minutes = BfTimer._compute_suggested_minutes(elapsed)
        h = int(suggested_minutes // 60)
        m = int(suggested_minutes % 60)
        elapsed_h = int(elapsed // 3600)
        elapsed_m = int((elapsed % 3600) // 60)
        elapsed_s = int(elapsed % 60)
        elapsed_display = f"{elapsed_h:02d}:{elapsed_m:02d}:{elapsed_s:02d}"
        wizard = self.env["bf.timer.stop.wizard"].create({
            "timer_id": timer.id,
            "project_name": BfTimer._project_label(timer.project_id)[0],
            "task_name": timer.task_id.name,
            "elapsed_display": elapsed_display,
            "hours": h,
            "minutes": m,
            "description": timer.description or timer.task_id.name,
        })
        return {
            "type": "ir.actions.act_window",
            "name": "Arrêter le timer",
            "res_model": "bf.timer.stop.wizard",
            "res_id": wizard.id,
            "views": [[False, "form"]],
            "target": "new",
        }
