from odoo import api, fields, models, tools


class AuditDashboard(models.Model):
    _name = "audit.dashboard"
    _description = "Tableau de bord Audit TI"
    _auto = False

    name = fields.Char()

    def init(self):
        tools.drop_view_if_exists(self.env.cr, "audit_dashboard")
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW audit_dashboard AS (
                SELECT 1 AS id, 'Audit TI Dashboard' AS name
            )
        """)

    @api.model
    def get_dashboard_data(self, include_delivered=False):
        return {
            "summary": self._get_summary(include_delivered),
            "progress_by_client": self._get_progress_by_client(include_delivered),
            "progress_by_supplier": self._get_progress_by_supplier(include_delivered),
            "status_distribution": self._get_status_distribution(include_delivered),
            "recent_assessments": self._get_recent_assessments(include_delivered),
            "open_watchpoints": self._get_open_watchpoints(include_delivered),
            "include_delivered": include_delivered,
        }

    @api.model
    def _get_summary(self, include_delivered=False):
        state_filter = "" if include_delivered else "AND c.state = 'in_progress'"
        self.env.cr.execute(f"""
            SELECT
                (SELECT COUNT(*) FROM audit_client c
                 WHERE c.active = true {state_filter}) AS total_clients,
                (SELECT COUNT(*) FROM audit_element) AS total_elements,
                (SELECT COUNT(*) FROM audit_assessment a
                 JOIN audit_client c ON c.id = a.client_id
                 WHERE c.active = true {state_filter}) AS total_assessments,
                (SELECT COUNT(*) FROM audit_assessment a
                 JOIN audit_client c ON c.id = a.client_id
                 WHERE a.status NOT IN ('pending')
                   AND c.active = true {state_filter}) AS assessed_count,
                (SELECT COUNT(*) FROM audit_assessment a
                 JOIN audit_client c ON c.id = a.client_id
                 WHERE a.status = 'adequate'
                   AND c.active = true {state_filter}) AS adequate_count,
                (SELECT COUNT(*) FROM audit_watchpoint w
                 JOIN audit_client c ON c.id = w.client_id
                 WHERE w.state != 'resolved'
                   AND c.active = true {state_filter}) AS open_watchpoints
        """)
        row = self.env.cr.dictfetchone()
        total = row["total_assessments"] or 1
        # Count distinct elements covered (adequate) across filtered clients
        self.env.cr.execute(f"""
            SELECT COUNT(DISTINCT (a.client_id, a.element_id))
            FROM audit_assessment a
            JOIN audit_client c ON c.id = a.client_id
            WHERE a.status = 'adequate'
              AND c.active = true {state_filter}
        """)
        covered = self.env.cr.fetchone()[0] or 0
        total_possible = row["total_clients"] * row["total_elements"]
        return {
            "total_clients": row["total_clients"],
            "total_elements": row["total_elements"],
            "total_assessments": row["total_assessments"],
            "assessed_count": row["assessed_count"],
            "adequate_count": row["adequate_count"],
            "open_watchpoints": row["open_watchpoints"],
            "covered_elements": covered,
            "total_possible_elements": total_possible,
            "pct_assessed": round(100 * row["assessed_count"] / total, 1) if total else 0,
            "pct_adequate": round(100 * row["adequate_count"] / total, 1) if total else 0,
            "pct_covered": round(100 * covered / total_possible, 1) if total_possible else 0,
        }

    @api.model
    def _get_progress_by_client(self, include_delivered=False):
        state_filter = "" if include_delivered else "AND c.state = 'in_progress'"
        self.env.cr.execute(f"""
            SELECT
                c.id,
                c.name,
                c.state,
                COUNT(a.id) AS total,
                COUNT(a.id) FILTER (WHERE a.status NOT IN ('pending')) AS assessed,
                COUNT(a.id) FILTER (WHERE a.status = 'adequate') AS adequate,
                COUNT(a.id) FILTER (WHERE a.status = 'inadequate') AS inadequate,
                COUNT(a.id) FILTER (WHERE a.status = 'partial') AS partial,
                COUNT(a.id) FILTER (WHERE a.status IN ('to_validate', 'declared', 'na')) AS other
            FROM audit_client c
            LEFT JOIN audit_assessment a ON a.client_id = c.id
            WHERE c.active = true {state_filter}
            GROUP BY c.id, c.name, c.state
            ORDER BY c.name
        """)
        results = []
        for row in self.env.cr.dictfetchall():
            total = row["total"] or 1
            results.append({
                "id": row["id"],
                "name": row["name"],
                "state": row["state"],
                "total": row["total"],
                "assessed": row["assessed"],
                "adequate": row["adequate"],
                "inadequate": row["inadequate"],
                "partial": row["partial"],
                "other": row["other"],
                "pct_assessed": round(100 * row["assessed"] / total, 1),
                "pct_adequate": round(100 * row["adequate"] / total, 1),
            })
        return results

    @api.model
    def _get_progress_by_supplier(self, include_delivered=False):
        state_filter = "" if include_delivered else "AND c.state = 'in_progress'"
        self.env.cr.execute(f"""
            SELECT
                s.id,
                s.name,
                s.nc_status,
                COUNT(a.id) AS total,
                COUNT(a.id) FILTER (WHERE a.status NOT IN ('pending')) AS assessed,
                COUNT(a.id) FILTER (WHERE a.status = 'adequate') AS adequate
            FROM audit_supplier s
            LEFT JOIN audit_assessment a ON a.supplier_id = s.id
            JOIN audit_client c ON c.id = a.client_id
            WHERE s.active = true AND c.active = true {state_filter}
            GROUP BY s.id, s.name, s.nc_status
            HAVING COUNT(a.id) > 0
            ORDER BY s.name
        """)
        results = []
        for row in self.env.cr.dictfetchall():
            total = row["total"] or 1
            results.append({
                "id": row["id"],
                "name": row["name"],
                "nc_status": row["nc_status"],
                "total": row["total"],
                "assessed": row["assessed"],
                "adequate": row["adequate"],
                "pct_assessed": round(100 * row["assessed"] / total, 1),
            })
        return results

    @api.model
    def _get_status_distribution(self, include_delivered=False):
        state_filter = "" if include_delivered else "AND c.state = 'in_progress'"
        self.env.cr.execute(f"""
            SELECT
                COALESCE(a.status, 'pending') AS status,
                COUNT(*) AS count
            FROM audit_assessment a
            JOIN audit_client c ON c.id = a.client_id
            WHERE c.active = true {state_filter}
            GROUP BY a.status
            ORDER BY a.status
        """)
        return self.env.cr.dictfetchall()

    @api.model
    def _get_recent_assessments(self, include_delivered=False):
        state_filter = "" if include_delivered else "AND c.state = 'in_progress'"
        self.env.cr.execute(f"""
            SELECT
                a.id,
                c.name AS client_name,
                s.name AS supplier_name,
                e.num AS element_num,
                e.name AS element_name,
                a.status,
                a.assessed_date
            FROM audit_assessment a
            JOIN audit_client c ON c.id = a.client_id
            JOIN audit_supplier s ON s.id = a.supplier_id
            JOIN audit_element e ON e.id = a.element_id
            WHERE a.status NOT IN ('pending')
              AND a.assessed_date IS NOT NULL
              AND c.active = true {state_filter}
            ORDER BY a.assessed_date DESC, a.write_date DESC
            LIMIT 20
        """)
        results = []
        for row in self.env.cr.dictfetchall():
            results.append({
                "id": row["id"],
                "client_name": row["client_name"],
                "supplier_name": row["supplier_name"],
                "element_num": row["element_num"],
                "element_name": row["element_name"],
                "status": row["status"],
                "assessed_date": str(row["assessed_date"]) if row["assessed_date"] else "",
            })
        return results

    @api.model
    def _get_open_watchpoints(self, include_delivered=False):
        state_filter = "" if include_delivered else "AND c.state = 'in_progress'"
        self.env.cr.execute(f"""
            SELECT
                w.id,
                c.name AS client_name,
                e.name AS element_name,
                w.description,
                w.priority,
                w.state
            FROM audit_watchpoint w
            JOIN audit_client c ON c.id = w.client_id
            LEFT JOIN audit_element e ON e.id = w.element_id
            WHERE w.state != 'resolved'
              AND c.active = true {state_filter}
            ORDER BY
                CASE w.priority
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                END,
                w.id
            LIMIT 20
        """)
        return self.env.cr.dictfetchall()

    @api.model
    def action_open_client(self, client_id):
        return {
            "type": "ir.actions.act_window",
            "name": "Client",
            "res_model": "audit.client",
            "res_id": client_id,
            "views": [[False, "form"]],
            "target": "current",
        }

    @api.model
    def action_open_assessments(self, domain=None):
        return {
            "type": "ir.actions.act_window",
            "name": "Évaluations",
            "res_model": "audit.assessment",
            "views": [[False, "list"], [False, "form"]],
            "domain": domain or [],
            "target": "current",
        }
