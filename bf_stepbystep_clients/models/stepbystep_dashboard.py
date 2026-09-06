import logging
from datetime import date, timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Default budget when project.allocated_hours is unset
DEFAULT_BUDGET_HOURS = 40

# A project carrying this tag (project.tags) is on hold. The label carries an
# emoji and has already shifted between singular and plural, so match on the
# stable fragment rather than the exact name.
PAUSED_TAG_FRAGMENT = "en pause"


def _classify_sector(project_name):
    """Classify a project into a sector based on its name."""
    name_lower = (project_name or "").lower()
    if "cpe " in name_lower or "garderie" in name_lower:
        return "cpe"
    if any(kw in name_lower for kw in (
        "obnl", "association", "fondation", "organisme",
        "centre communautaire", "maison des jeunes",
    )):
        return "obnl"
    if any(kw in name_lower for kw in ("école", "css ", "commission scolaire")):
        return "scolaire"
    if any(kw in name_lower for kw in (
        "administration", "marketing",
        "portail", "interne",
    )):
        return "interne"
    return "entreprise"


def _abbreviate(name):
    """Abbreviate a full name: 'Marie Tremblay' → 'Marie T.'"""
    if not name:
        return ""
    if ", " in name:
        name = name.split(", ", 1)[-1]
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    return name


def _budget_status(pct):
    if pct > 100:
        return "over"
    if pct >= 90:
        return "critical"
    if pct >= 75:
        return "warning"
    return "ok"


def _activity_status(days):
    if days is None:
        return "red"
    if days <= 7:
        return "green"
    if days <= 14:
        return "amber"
    return "red"


class BfStepbystepDashboard(models.AbstractModel):
    _name = "bf.stepbystep.dashboard"
    _description = "Tableau de bord Step-by-Step (suivi d'accompagnement client)"
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve.

    # ------------------------------------------------------------------
    # Public API — called from OWL client action
    # ------------------------------------------------------------------

    @api.model
    def get_dashboard_data(self):
        """Return all dashboard data for the OWL component."""
        today = date.today()

        projects = self._get_projects()
        hours_map = self._get_hours_by_project()
        activity_map = self._get_last_activity_by_project()
        task_count_map = self._get_task_counts_by_project()
        step_progress_map = self._get_step_progress_by_project()
        next_deadline_map = self._get_next_deadline_by_project()

        project_list = []
        summary = {
            "total_projects": 0,
            "total_hours": 0.0,
            "over_budget": 0,
            "critical_budget": 0,
            "warning_budget": 0,
            "inactive_count": 0,
            "stale_count": 0,
            "completed_count": 0,
            "paused_count": 0,
        }

        for proj in projects:
            pid = proj["id"]
            name = proj["name"] or ""
            is_paused = bool(proj.get("is_paused"))
            budget = proj.get("allocated_hours") or DEFAULT_BUDGET_HOURS
            hours = hours_map.get(pid, 0.0)
            budget_pct = round((hours / budget) * 100, 1) if budget else 0
            b_status = _budget_status(budget_pct)

            activity = activity_map.get(pid, {})
            last_date = activity.get("date")
            last_user = activity.get("user", "")
            if last_date:
                lat_days = (today - last_date).days
            else:
                lat_days = -1
            a_status = _activity_status(lat_days if lat_days >= 0 else None)

            task_count = task_count_map.get(pid, 0)
            step_progress = step_progress_map.get(pid, {})

            d_start = proj.get("date_start")
            d_end = proj.get("date_end")
            timeline_pct = 0
            if d_start and d_end:
                total_days = (d_end - d_start).days
                elapsed = (today - d_start).days
                if total_days > 0:
                    timeline_pct = min(100, round((elapsed / total_days) * 100))

            # Current step = lowest step number that is not 100% done.
            # Global progress = average of step percentages (steps with tasks).
            current_step_label = ""
            global_progress = 0
            step_count = 0
            for step_num in sorted(step_progress.keys()):
                step = step_progress[step_num]
                if step["total"] <= 0:
                    continue
                step_count += 1
                global_progress += step["pct"]
                if step["pct"] < 100 and not current_step_label:
                    label = step.get("name") or f"Étape {step_num}"
                    current_step_label = f"Étape {step_num} — {label}" if step.get("name") else f"Étape {step_num}"

            if step_count > 0:
                global_progress = round(global_progress / step_count, 0)

            # A mandate is "complete" when every progression step that has tasks
            # is fully done (avg of step pcts == 100). Completed projects are
            # pulled out of the at-risk buckets (Inactifs / Ralentis) below: a
            # finished mandate naturally has no recent timesheets, so it would
            # otherwise be mislabelled as inactive.
            is_complete = bool(step_count > 0 and global_progress >= 100)

            # A paused mandate belongs to its own category, not to the sector
            # inferred from its name. Like completed mandates, it is kept out of
            # the at-risk buckets below: a mandate on hold has no recent
            # timesheets and a frozen budget, so it would sit in Inactifs (and
            # possibly a budget alert) forever.
            sector = "pause" if is_paused else _classify_sector(name)

            nxt = next_deadline_map.get(pid, {})
            nxt_date = nxt.get("date")
            nxt_days = (nxt_date - today).days if nxt_date else -1
            nxt_label = nxt.get("label", "")

            project_list.append({
                "id": pid,
                "name": name,
                "partner_name": proj.get("partner_name", ""),
                "partner_id": proj.get("partner_id") or 0,
                "sector": sector,
                "hours_consumed": round(hours, 2),
                "budget_hours": budget,
                "budget_pct": budget_pct,
                "budget_status": b_status,
                "last_activity_date": str(last_date) if last_date else "",
                "last_activity_user": last_user,
                "last_activity_days": lat_days,
                "activity_status": a_status,
                "task_count": task_count,
                "current_module": current_step_label,
                "progress_pct": int(global_progress),
                "completed": is_complete,
                "paused": is_paused,
                "date_start": str(proj.get("date_start") or ""),
                "date_end": str(proj.get("date_end") or ""),
                "timeline_pct": timeline_pct,
                "next_deadline_date": str(nxt_date) if nxt_date else "",
                "next_deadline_days": nxt_days,
                "next_deadline_label": nxt_label,
            })

            summary["total_projects"] += 1
            summary["total_hours"] += hours

            if is_paused:
                summary["paused_count"] += 1
                continue

            if b_status == "over":
                summary["over_budget"] += 1
            elif b_status == "critical":
                summary["critical_budget"] += 1
            elif b_status == "warning":
                summary["warning_budget"] += 1
            if is_complete:
                summary["completed_count"] += 1
            elif a_status == "red":
                summary["inactive_count"] += 1
            elif a_status == "amber":
                summary["stale_count"] += 1

        summary["total_hours"] = round(summary["total_hours"], 1)

        return {
            "summary": summary,
            "projects": project_list,
        }

    @api.model
    def get_client_detail(self, project_id):
        """Return detailed data for a single client project."""
        today = date.today()

        Project = self.env["project.project"]
        proj = Project.browse(project_id)
        if not proj.exists():
            return {"error": "Project not found"}
        # Enforce read access (record rules + company scope) before the raw
        # SQL below, so an arbitrary project_id can't exfiltrate another
        # company's / user's mandate detail.
        try:
            proj.check_access_rights("read")
            proj.check_access_rule("read")
        except Exception:
            return {"error": "Project not found"}

        budget = proj.allocated_hours or DEFAULT_BUDGET_HOURS

        self.env.cr.execute("""
            SELECT COALESCE(SUM(unit_amount), 0)
            FROM account_analytic_line
            WHERE project_id = %s
        """, (project_id,))
        hours = self.env.cr.fetchone()[0] or 0.0
        budget_pct = round((hours / budget) * 100, 1) if budget else 0

        date_start = proj.date_start
        date_end = proj.date
        timeline_pct = 0
        if date_start and date_end:
            total_days = (date_end - date_start).days
            elapsed = (today - date_start).days
            if total_days > 0:
                timeline_pct = min(100, round((elapsed / total_days) * 100, 1))

        # Step progress: read progression_step_number / progression_step_name
        # directly from project.task.type. No more hardcoded keyword map.
        self.env.cr.execute("""
            SELECT
                ptt.progression_step_number AS step_num,
                COALESCE(ptt.progression_step_name->>'fr_CA',
                         ptt.progression_step_name->>'en_US',
                         ptt.progression_step_name #>> '{}',
                         ptt.name->>'fr_CA',
                         ptt.name->>'en_US',
                         '') AS step_name,
                pt.state,
                COUNT(*) AS cnt
            FROM project_task pt
            JOIN project_task_type ptt ON ptt.id = pt.stage_id
            WHERE pt.project_id = %s
              AND pt.active = true
              AND ptt.progression_step_number > 0
            GROUP BY ptt.progression_step_number, ptt.progression_step_name,
                     ptt.name, pt.state
            ORDER BY ptt.progression_step_number
        """, (project_id,))
        rows = self.env.cr.dictfetchall()

        step_data = {}
        for row in rows:
            step_num = row["step_num"]
            if step_num is None or step_num <= 0:
                continue
            if step_num not in step_data:
                step_data[step_num] = {
                    "total": 0,
                    "completed": 0,
                    "name": row["step_name"] or f"Étape {step_num}",
                }
            step_data[step_num]["total"] += row["cnt"]
            if row["state"] in ("1_done", "1_canceled"):
                step_data[step_num]["completed"] += row["cnt"]

        modules = []
        for step_num in sorted(step_data.keys()):
            data = step_data[step_num]
            total = data["total"]
            completed = data["completed"]
            pct = round((completed / total) * 100) if total > 0 else 0

            if pct >= 100:
                status = "done"
            elif pct > 0:
                status = "current"
            elif total > 0:
                status = "pending"
            else:
                status = "upcoming"

            modules.append({
                "number": step_num,
                "name": data["name"],
                "total_tasks": total,
                "completed_tasks": completed,
                "progress_pct": pct,
                "status": status,
            })

        # Overdue tasks
        self.env.cr.execute("""
            SELECT pt.id, pt.name, pt.date_deadline,
                   COALESCE(ptt.name->>'fr_CA', ptt.name->>'en_US', '') AS stage_name
            FROM project_task pt
            LEFT JOIN project_task_type ptt ON ptt.id = pt.stage_id
            WHERE pt.project_id = %s
              AND pt.active = true
              AND pt.state NOT IN ('1_done', '1_canceled')
              AND pt.date_deadline < %s
            ORDER BY pt.date_deadline
            LIMIT 20
        """, (project_id, str(today)))
        overdue_tasks = [
            {
                "id": r["id"],
                "name": r["name"],
                "deadline": str(r["date_deadline"]),
                "stage": r["stage_name"],
            }
            for r in self.env.cr.dictfetchall()
        ]

        # Upcoming tasks (next 60 days)
        future = today + timedelta(days=60)
        self.env.cr.execute("""
            SELECT pt.id, pt.name, pt.date_deadline,
                   COALESCE(ptt.name->>'fr_CA', ptt.name->>'en_US', '') AS stage_name
            FROM project_task pt
            LEFT JOIN project_task_type ptt ON ptt.id = pt.stage_id
            WHERE pt.project_id = %s
              AND pt.active = true
              AND pt.state NOT IN ('1_done', '1_canceled')
              AND pt.date_deadline >= %s
              AND pt.date_deadline <= %s
            ORDER BY pt.date_deadline
            LIMIT 20
        """, (project_id, str(today), str(future)))
        upcoming_tasks = [
            {
                "id": r["id"],
                "name": r["name"],
                "deadline": str(r["date_deadline"]),
                "stage": r["stage_name"],
            }
            for r in self.env.cr.dictfetchall()
        ]

        # Recent activity: timesheets + chatter messages
        self.env.cr.execute("""
            SELECT aal.date::text AS activity_date,
                   COALESCE(he.name, '') AS user_name,
                   COALESCE(pt.name, '') AS description,
                   aal.unit_amount AS hours,
                   'timesheet' AS activity_type
            FROM account_analytic_line aal
            LEFT JOIN hr_employee he ON he.id = aal.employee_id
            LEFT JOIN project_task pt ON pt.id = aal.task_id
            WHERE aal.project_id = %s
            ORDER BY aal.date DESC
            LIMIT 10
        """, (project_id,))
        ts_rows = self.env.cr.dictfetchall()

        self.env.cr.execute("""
            SELECT mm.date::date::text AS activity_date,
                   COALESCE(rp.name, '') AS user_name,
                   COALESCE(mm.subject, COALESCE(pt.name, '')) AS description,
                   0 AS hours,
                   mm.message_type AS activity_type
            FROM mail_message mm
            JOIN project_task pt ON pt.id = mm.res_id
                AND mm.model = 'project.task'
            LEFT JOIN res_partner rp ON rp.id = mm.author_id
            WHERE pt.project_id = %s
              AND mm.message_type IN ('email', 'comment')
            ORDER BY mm.date DESC
            LIMIT 10
        """, (project_id,))
        msg_rows = self.env.cr.dictfetchall()

        all_activity = []
        for r in ts_rows:
            all_activity.append({
                "date": r["activity_date"],
                "hours": round(r["hours"], 2),
                "employee": r["user_name"],
                "task": r["description"],
                "type": "timesheet",
            })
        for r in msg_rows:
            all_activity.append({
                "date": r["activity_date"],
                "hours": 0,
                "employee": _abbreviate(r["user_name"]),
                "task": r["description"] or "",
                "type": r["activity_type"],
            })
        all_activity.sort(key=lambda x: x["date"], reverse=True)
        recent_activity = all_activity[:15]

        return {
            "project": {
                "id": project_id,
                "name": proj.name,
                "partner_id": proj.partner_id.id if proj.partner_id else 0,
                "partner_name": proj.partner_id.name if proj.partner_id else "",
                "date_start": str(date_start) if date_start else "",
                "date_end": str(date_end) if date_end else "",
                "timeline_pct": timeline_pct,
                "hours_consumed": round(hours, 2),
                "budget_hours": budget,
                "budget_pct": budget_pct,
                "remaining_hours": round(max(0, budget - hours), 2),
            },
            "modules": modules,
            "overdue_tasks": overdue_tasks,
            "upcoming_tasks": upcoming_tasks,
            "recent_activity": recent_activity,
        }

    # ------------------------------------------------------------------
    # Navigation actions
    # ------------------------------------------------------------------

    @api.model
    def action_open_project(self, project_id):
        return {
            "type": "ir.actions.act_window",
            "name": "Tâches du projet",
            "res_model": "project.task",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("project_id", "=", project_id)],
            "context": {"default_project_id": project_id},
        }

    @api.model
    def action_open_task(self, task_id):
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "res_id": task_id,
            "views": [[False, "form"]],
        }

    @api.model
    def action_open_partner(self, partner_id):
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": partner_id,
            "views": [[False, "form"]],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @api.model
    def _get_projects(self):
        """Get active client projects."""
        self.env.cr.execute("""
            SELECT pp.id,
                   COALESCE(pp.name->>'fr_CA', pp.name->>'en_US',
                            pp.name #>> '{}') AS name,
                   COALESCE(rp.name, '') AS partner_name,
                   pp.partner_id,
                   pp.date_start, pp.date AS date_end,
                   COALESCE(pp.allocated_hours, 0) AS allocated_hours,
                   EXISTS (
                       SELECT 1
                       FROM project_project_project_tags_rel rel
                       JOIN project_tags pt ON pt.id = rel.project_tags_id
                       WHERE rel.project_project_id = pp.id
                         AND lower(COALESCE(pt.name->>'fr_CA',
                                            pt.name->>'en_US',
                                            pt.name #>> '{}', '')) LIKE %s
                   ) AS is_paused
            FROM project_project pp
            LEFT JOIN res_partner rp ON rp.id = pp.partner_id
            WHERE pp.active = true
              AND (pp.date IS NULL OR pp.date >= CURRENT_DATE)
              AND (pp.company_id IS NULL OR pp.company_id = ANY(%s))
            ORDER BY COALESCE(pp.name->>'fr_CA', pp.name->>'en_US')
        """, (f"%{PAUSED_TAG_FRAGMENT}%", self.env.companies.ids))
        return self.env.cr.dictfetchall()

    @api.model
    def _get_hours_by_project(self):
        self.env.cr.execute("""
            SELECT project_id, SUM(unit_amount) AS total_hours
            FROM account_analytic_line
            WHERE project_id IS NOT NULL
            GROUP BY project_id
        """)
        return {r["project_id"]: r["total_hours"] for r in self.env.cr.dictfetchall()}

    @api.model
    def _get_last_activity_by_project(self):
        """Most recent activity per project — timesheets + chatter merged."""
        self.env.cr.execute("""
            SELECT DISTINCT ON (aal.project_id)
                aal.project_id,
                aal.date,
                COALESCE(rp.name, '') AS user_name
            FROM account_analytic_line aal
            LEFT JOIN res_users ru ON ru.id = aal.user_id
            LEFT JOIN res_partner rp ON rp.id = ru.partner_id
            WHERE aal.project_id IS NOT NULL
            ORDER BY aal.project_id, aal.date DESC
        """)
        result = {}
        for r in self.env.cr.dictfetchall():
            result[r["project_id"]] = {
                "date": r["date"],
                "user": _abbreviate(r["user_name"]),
            }

        self.env.cr.execute("""
            SELECT DISTINCT ON (pt.project_id)
                pt.project_id,
                mm.date::date AS activity_date,
                COALESCE(rp.name, '') AS user_name
            FROM mail_message mm
            JOIN project_task pt ON pt.id = mm.res_id
                AND mm.model = 'project.task'
            LEFT JOIN res_partner rp ON rp.id = mm.author_id
            WHERE mm.message_type IN ('email', 'comment')
              AND pt.project_id IS NOT NULL
            ORDER BY pt.project_id, mm.date DESC
        """)
        for r in self.env.cr.dictfetchall():
            pid = r["project_id"]
            chatter_date = r["activity_date"]
            existing = result.get(pid)
            if not existing or (chatter_date and chatter_date > existing["date"]):
                result[pid] = {
                    "date": chatter_date,
                    "user": _abbreviate(r["user_name"]),
                }

        return result

    @api.model
    def _get_task_counts_by_project(self):
        self.env.cr.execute("""
            SELECT project_id, COUNT(*) AS cnt
            FROM project_task
            WHERE active = true
            GROUP BY project_id
        """)
        return {r["project_id"]: r["cnt"] for r in self.env.cr.dictfetchall()}

    @api.model
    def _get_step_progress_by_project(self):
        """Per-project step progress, keyed by step number.

        Reads progression_step_number / progression_step_name directly from
        project.task.type. Stages with progression_step_number = 0 are
        excluded from the progression visualization.
        """
        self.env.cr.execute("""
            SELECT
                pt.project_id,
                ptt.progression_step_number AS step_num,
                COALESCE(ptt.progression_step_name->>'fr_CA',
                         ptt.progression_step_name->>'en_US',
                         ptt.progression_step_name #>> '{}',
                         ptt.name->>'fr_CA',
                         ptt.name->>'en_US',
                         '') AS step_name,
                pt.state,
                COUNT(*) AS cnt
            FROM project_task pt
            JOIN project_task_type ptt ON ptt.id = pt.stage_id
            WHERE pt.active = true
              AND ptt.progression_step_number > 0
            GROUP BY pt.project_id, ptt.progression_step_number,
                     ptt.progression_step_name, ptt.name, pt.state
        """)

        result = {}
        for row in self.env.cr.dictfetchall():
            pid = row["project_id"]
            step_num = row["step_num"]
            if step_num is None or step_num <= 0:
                continue

            if pid not in result:
                result[pid] = {}
            if step_num not in result[pid]:
                result[pid][step_num] = {
                    "total": 0,
                    "completed": 0,
                    "pct": 0,
                    "name": row["step_name"] or f"Étape {step_num}",
                }

            result[pid][step_num]["total"] += row["cnt"]
            if row["state"] in ("1_done", "1_canceled"):
                result[pid][step_num]["completed"] += row["cnt"]

        for pid in result:
            for step_num in result[pid]:
                step = result[pid][step_num]
                if step["total"] > 0:
                    step["pct"] = round((step["completed"] / step["total"]) * 100)

        return result

    @api.model
    def _get_next_deadline_by_project(self):
        """Soonest upcoming deadline per project — activities + task deadlines."""
        today_str = str(date.today())

        self.env.cr.execute("""
            SELECT DISTINCT ON (pt.project_id)
                pt.project_id,
                ma.date_deadline,
                COALESCE(mat.name->>'fr_CA', mat.name->>'en_US',
                         mat.name #>> '{}', '') AS type_name,
                COALESCE(ma.summary, '') AS summary
            FROM mail_activity ma
            JOIN project_task pt ON pt.id = ma.res_id
                AND ma.res_model = 'project.task'
            LEFT JOIN mail_activity_type mat ON mat.id = ma.activity_type_id
            WHERE pt.project_id IS NOT NULL
              AND ma.date_deadline >= %s
            ORDER BY pt.project_id, ma.date_deadline
        """, (today_str,))
        result = {}
        for r in self.env.cr.dictfetchall():
            label = r["summary"] or r["type_name"] or "Activité"
            result[r["project_id"]] = {
                "date": r["date_deadline"],
                "label": label,
            }

        self.env.cr.execute("""
            SELECT DISTINCT ON (pt.project_id)
                pt.project_id,
                pt.date_deadline::date AS date_deadline,
                pt.name AS task_name
            FROM project_task pt
            WHERE pt.active = true
              AND pt.state NOT IN ('1_done', '1_canceled')
              AND pt.date_deadline >= %s
              AND pt.project_id IS NOT NULL
            ORDER BY pt.project_id, pt.date_deadline::date
        """, (today_str,))
        for r in self.env.cr.dictfetchall():
            pid = r["project_id"]
            existing = result.get(pid)
            if not existing or r["date_deadline"] < existing["date"]:
                result[pid] = {
                    "date": r["date_deadline"],
                    "label": r["task_name"] or "Tâche",
                }

        return result
