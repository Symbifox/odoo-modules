import logging
from datetime import date, timedelta

from odoo import api, models

_logger = logging.getLogger(__name__)

# Fallback bar duration (days) for tasks that have no deadline.
DEFAULT_TASK_DAYS = 7
# Padding (days) added on each side of the computed date range.
RANGE_PADDING_DAYS = 3


def _abbreviate(name):
    """'Marie Tremblay' -> 'Marie T.' — mirror of the Step-by-Step helper."""
    if not name:
        return ""
    if ", " in name:
        name = name.split(", ", 1)[-1]
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    return name


def _task_status(state, deadline, today):
    """Classify a task for colouring the Gantt bar."""
    if state == "1_done":
        return "done"
    if state == "1_canceled":
        return "canceled"
    if deadline and deadline < today and state not in ("1_done", "1_canceled"):
        return "overdue"
    if state == "01_in_progress":
        return "in_progress"
    return "upcoming"


def _task_pct(state, allocated_hours, progress):
    """Bar fill percentage (0-100).

    `progress` is the hr_timesheet ratio (effective/allocated, e.g. 0.6 = 60%,
    can exceed 1.0 for overtime). Completed/cancelled tasks are always full.
    """
    if state in ("1_done", "1_canceled"):
        return 100
    if allocated_hours and allocated_hours > 0:
        return max(0, min(100, round((progress or 0.0) * 100)))
    return 0


class BfProgressionGantt(models.Model):
    _name = "bf.progression.gantt"
    _description = "Données Échéancier Gantt (progression des projets)"
    _auto = False

    # ------------------------------------------------------------------
    # Public API — called from the OWL client action
    # ------------------------------------------------------------------

    @api.model
    def get_portfolio(self):
        """Light list of active mandates for the standalone project picker."""
        self.env.cr.execute("""
            SELECT pp.id,
                   COALESCE(pp.name->>'fr_CA', pp.name->>'en_US',
                            pp.name #>> '{}') AS name,
                   COALESCE(rp.name, '') AS partner_name
            FROM project_project pp
            LEFT JOIN res_partner rp ON rp.id = pp.partner_id
            WHERE pp.active = true
              AND (pp.date IS NULL OR pp.date >= CURRENT_DATE)
            ORDER BY name
        """)
        return [
            {
                "id": r["id"],
                "name": r["name"] or "Projet",
                "partner_name": r["partner_name"],
            }
            for r in self.env.cr.dictfetchall()
        ]

    @api.model
    def get_project_gantt(self, project_id):
        """Return task-level Gantt data for a single project.

        Bars are derived read-only from existing task dates and grouped by the
        progression step of their stage (same steps as bf_stepbystep_clients).
        """
        today = date.today()

        Project = self.env["project.project"]
        proj = Project.browse(project_id)
        if not proj.exists():
            return {"error": "Project not found"}

        # --- Tasks ---
        self.env.cr.execute("""
            SELECT
                pt.id,
                pt.name,
                pt.state,
                COALESCE(ptt.progression_step_number, 0) AS step_num,
                COALESCE(ptt.progression_step_name->>'fr_CA',
                         ptt.progression_step_name->>'en_US',
                         ptt.progression_step_name #>> '{}',
                         ptt.name->>'fr_CA',
                         ptt.name->>'en_US',
                         '') AS step_name,
                COALESCE(pt.date_assign::date, pt.create_date::date) AS start_date,
                pt.date_deadline::date AS deadline_date,
                COALESCE(pt.allocated_hours, 0) AS allocated_hours,
                COALESCE(pt.effective_hours, 0) AS effective_hours,
                COALESCE(pt.progress, 0) AS progress
            FROM project_task pt
            LEFT JOIN project_task_type ptt ON ptt.id = pt.stage_id
            WHERE pt.project_id = %s
              AND pt.active = true
            ORDER BY COALESCE(ptt.progression_step_number, 0),
                     pt.date_deadline NULLS LAST, pt.id
        """, (project_id,))
        rows = self.env.cr.dictfetchall()

        task_ids = [r["id"] for r in rows]
        assignee_map = self._get_assignees(task_ids)
        deps = self._get_dependencies(task_ids)

        tasks = []
        steps = {}
        min_date = None
        max_date = None

        for r in rows:
            start = r["start_date"] or today
            end = r["deadline_date"] or (start + timedelta(days=DEFAULT_TASK_DAYS))
            if end < start:
                end = start
            status = _task_status(r["state"], r["deadline_date"], today)
            pct = _task_pct(r["state"], r["allocated_hours"], r["progress"])
            step_num = r["step_num"] or 0

            tasks.append({
                "id": r["id"],
                "name": r["name"] or "Tâche",
                "step_num": step_num,
                "start": str(start),
                "end": str(end),
                "deadline": str(r["deadline_date"]) if r["deadline_date"] else "",
                "progress": pct,
                "status": status,
                "assignee": assignee_map.get(r["id"], ""),
                "allocated_hours": round(r["allocated_hours"], 2),
                "effective_hours": round(r["effective_hours"], 2),
            })

            min_date = start if min_date is None else min(min_date, start)
            max_date = end if max_date is None else max(max_date, end)

            # Step aggregation (completed = done/canceled, like the existing
            # module). Step 0 = stages with no progression_step_number → a
            # single trailing "Hors étape" lane (never borrow a stage name).
            if step_num:
                lane_name = r["step_name"] or f"Étape {step_num}"
            else:
                lane_name = "Hors étape"
            s = steps.setdefault(step_num, {
                "number": step_num,
                "name": lane_name,
                "total": 0,
                "completed": 0,
            })
            s["total"] += 1
            if r["state"] in ("1_done", "1_canceled"):
                s["completed"] += 1

        step_list = []
        # Configured steps first (by number), the "Hors étape" lane (0) last.
        for step_num in sorted(steps.keys(), key=lambda n: (n == 0, n)):
            s = steps[step_num]
            s["pct"] = round((s["completed"] / s["total"]) * 100) if s["total"] else 0
            step_list.append(s)

        # --- Date range (fallback to project dates / today window) ---
        if min_date is None:
            min_date = proj.date_start or today
            max_date = proj.date or (today + timedelta(days=30))
        min_date = min_date - timedelta(days=RANGE_PADDING_DAYS)
        max_date = max_date + timedelta(days=RANGE_PADDING_DAYS)
        if max_date <= min_date:
            max_date = min_date + timedelta(days=DEFAULT_TASK_DAYS)

        return {
            "project": {
                "id": proj.id,
                "name": proj.name,
                "partner_name": proj.partner_id.name if proj.partner_id else "",
                "date_start": str(proj.date_start) if proj.date_start else "",
                "date_end": str(proj.date) if proj.date else "",
            },
            "steps": step_list,
            "tasks": tasks,
            "deps": deps,
            "range": {
                "min": str(min_date),
                "max": str(max_date),
                "today": str(today),
            },
        }

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    @api.model
    def action_open_task(self, task_id):
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "res_id": task_id,
            "views": [[False, "form"]],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @api.model
    def _get_assignees(self, task_ids):
        """task_id -> abbreviated assignee names ('Marie T., Alex L.')."""
        if not task_ids:
            return {}
        self.env.cr.execute("""
            SELECT rel.task_id, rp.name
            FROM project_task_user_rel rel
            JOIN res_users ru ON ru.id = rel.user_id
            JOIN res_partner rp ON rp.id = ru.partner_id
            WHERE rel.task_id IN %s
            ORDER BY rel.task_id
        """, (tuple(task_ids),))
        result = {}
        for r in self.env.cr.dictfetchall():
            result.setdefault(r["task_id"], []).append(_abbreviate(r["name"]))
        return {tid: ", ".join(names) for tid, names in result.items()}

    @api.model
    def _get_dependencies(self, task_ids):
        """Edges depends_on_id -> task_id, restricted to the project's tasks."""
        if not task_ids:
            return []
        self.env.cr.execute("""
            SELECT task_id, depends_on_id
            FROM task_dependencies_rel
            WHERE task_id IN %s
        """, (tuple(task_ids),))
        id_set = set(task_ids)
        return [
            {"from": r["depends_on_id"], "to": r["task_id"]}
            for r in self.env.cr.dictfetchall()
            if r["depends_on_id"] in id_set
        ]
