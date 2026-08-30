import logging
from datetime import datetime, timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class BfEmailDashboard(models.AbstractModel):
    _name = "bf.email.dashboard"
    _description = "Tableau de bord courriels"
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve.

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @api.model
    def get_dashboard_data(self, date_from=False, date_to=False):
        """Return all dashboard KPIs for the OWL component.

        Args:
            date_from: Optional start date string (YYYY-MM-DD).
            date_to: Optional end date string (YYYY-MM-DD).
        """
        return {
            "volume": self._get_volume(date_from, date_to),
            "status": self._get_status_counts(date_from, date_to),
            "categories": self._get_category_counts(date_from, date_to),
            "response_time": self._get_response_time(date_from, date_to),
            "top_partners": self._get_top_partners(date_from, date_to),
            "daily_volume": self._get_daily_volume(date_from, date_to),
            "actionable": self._get_actionable(date_from, date_to),
            "handled_rate": self._get_handled_rate(date_from, date_to),
        }

    # ------------------------------------------------------------------
    # Navigation actions
    # ------------------------------------------------------------------

    @api.model
    def action_view_unread(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Courriels non lus",
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("user_id", "=", self.env.uid), ("status", "=", "new")],
        }

    # ------------------------------------------------------------------
    # Actionable card navigation
    # ------------------------------------------------------------------
    @api.model
    def action_view_inbox_active(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Boîte de réception active",
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "kanban"], [False, "form"]],
            # Même définition que l'arbre et le mobile : voir
            # `bf.email._inbox_domain`.
            "domain": [("user_id", "=", self.env.uid)]
            + self.env["bf.email"]._inbox_domain(),
        }

    @api.model
    def action_view_awaiting_reply(self):
        return {
            "type": "ir.actions.act_window",
            "name": "En attente de réponse",
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("user_id", "=", self.env.uid),
                ("direction", "=", "in"),
                ("status", "in", ("new", "read")),
                ("is_handled", "=", False),
                ("external_age_hours", ">=", 24),
            ],
        }

    @api.model
    def action_view_unrouted_orphans(self):
        return {
            "type": "ir.actions.act_window",
            "name": "IMAP orphelins à router",
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("user_id", "=", self.env.uid),
                ("source", "=", "imap"),
                ("res_model", "=", False),
                ("is_handled", "=", False),
            ],
        }

    @api.model
    def action_view_vip_pending(self):
        return {
            "type": "ir.actions.act_window",
            "name": "VIP en attente",
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("user_id", "=", self.env.uid),
                ("direction", "=", "in"),
                ("is_handled", "=", False),
                ("priority", "in", ("2", "3")),
            ],
        }

    @api.model
    def action_view_received(self, date_from=False, date_to=False):
        domain = [("user_id", "=", self.env.uid), ("direction", "=", "in")]
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to + " 23:59:59"))
        return {
            "type": "ir.actions.act_window",
            "name": "Re\u00e7us",
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "form"]],
            "domain": domain,
        }

    @api.model
    def action_view_sent(self, date_from=False, date_to=False):
        domain = [("user_id", "=", self.env.uid), ("direction", "=", "out")]
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to + " 23:59:59"))
        return {
            "type": "ir.actions.act_window",
            "name": "Envoy\u00e9s",
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "form"]],
            "domain": domain,
        }

    @api.model
    def action_view_by_category(self, category, date_from=False, date_to=False):
        labels = {
            "client": "Clients",
            "internal": "Internes",
            "vendor": "Fournisseurs",
            "notification": "Notifications",
            "marketing": "Marketing",
            "uncategorized": "Non cat\u00e9goris\u00e9s",
        }
        domain = [("user_id", "=", self.env.uid)]
        if category == "uncategorized":
            domain.append(("category", "=", False))
        else:
            domain.append(("category", "=", category))
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to + " 23:59:59"))
        return {
            "type": "ir.actions.act_window",
            "name": labels.get(category, category),
            "res_model": "bf.email",
            "views": [[False, "list"], [False, "form"]],
            "domain": domain,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @api.model
    def _date_domain(self, date_from, date_to):
        """Build ORM domain clauses for a date range.

        Always carries a ``user_id`` leaf so ORM-based KPIs stay pinned to
        the current user even for group_email_admin members (whose read-all
        ir.rule would otherwise blend everyone into the dashboard, while
        the raw-SQL KPIs stay per-user — inconsistent numbers).
        """
        domain = [("user_id", "=", self.env.uid)]
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to + " 23:59:59"))
        return domain

    @api.model
    def _sql_date_clause(self, date_from, date_to, table_alias="be"):
        """Build SQL WHERE clause and params for a date range.

        Always appends a ``{alias}.user_id = %s`` filter scoped to the
        current user so direct-SQL aggregates respect per-user isolation
        (record rules don't apply to raw cr.execute calls).
        """
        if not table_alias.isidentifier():
            raise ValueError("Invalid table alias")
        clauses = []
        params = []
        if date_from:
            clauses.append(f"{table_alias}.date >= %s")
            params.append(date_from)
        if date_to:
            clauses.append(f"{table_alias}.date <= %s")
            params.append(date_to + " 23:59:59")
        clauses.append(f"{table_alias}.user_id = %s")
        params.append(self.env.uid)
        return (" AND ".join(clauses), params)

    @api.model
    def _get_volume(self, date_from=False, date_to=False):
        """Email counts for the selected period."""
        BfEmail = self.env["bf.email"]
        dd = self._date_domain(date_from, date_to)
        return {
            "received": BfEmail.search_count([("direction", "=", "in")] + dd),
            "sent": BfEmail.search_count([("direction", "=", "out")] + dd),
            "total": BfEmail.search_count(dd) if dd else BfEmail.search_count([]),
        }

    @api.model
    def _get_status_counts(self, date_from=False, date_to=False):
        """Count by status. ``handled`` mirrors the legacy ``archived`` slot
        (rows traited via the new is_handled boolean)."""
        BfEmail = self.env["bf.email"]
        dd = self._date_domain(date_from, date_to)
        return {
            "new": BfEmail.search_count([("status", "=", "new")] + dd),
            "read": BfEmail.search_count([("status", "=", "read")] + dd),
            "replied": BfEmail.search_count([("status", "=", "replied")] + dd),
            "handled": BfEmail.search_count([("is_handled", "=", True)] + dd),
        }

    @api.model
    def _get_actionable(self, date_from=False, date_to=False):
        """Inbox-Zero actionable counts. Period filter respected."""
        BfEmail = self.env["bf.email"]
        dd = self._date_domain(date_from, date_to)
        return {
            "inbox_active": BfEmail.search_count([
                ("is_handled", "=", False),
                "|", ("imap_in_inbox", "=", True),
                     ("source", "in", ("chatter", "gateway")),
            ] + dd),
            "awaiting_reply": BfEmail.search_count([
                ("direction", "=", "in"),
                ("status", "in", ("new", "read")),
                ("is_handled", "=", False),
                ("external_age_hours", ">=", 24),
            ] + dd),
            "unrouted_orphans": BfEmail.search_count([
                ("source", "=", "imap"),
                ("res_model", "=", False),
                ("is_handled", "=", False),
            ] + dd),
            "vip_pending": BfEmail.search_count([
                ("direction", "=", "in"),
                ("is_handled", "=", False),
                ("priority", "in", ("2", "3")),
            ] + dd),
        }

    @api.model
    def _get_handled_rate(self, date_from=False, date_to=False):
        """Handled / total ratio for the selected period."""
        BfEmail = self.env["bf.email"]
        dd = self._date_domain(date_from, date_to)
        total = BfEmail.search_count(dd) if dd else BfEmail.search_count([])
        if not total:
            return {"total": 0, "handled": 0, "rate": 0}
        handled = BfEmail.search_count([("is_handled", "=", True)] + dd)
        return {
            "total": total,
            "handled": handled,
            "rate": round(100.0 * handled / total, 1),
        }

    @api.model
    def _get_category_counts(self, date_from=False, date_to=False):
        """Count by category (active emails only)."""
        date_clause, params = self._sql_date_clause(date_from, date_to)
        self.env.cr.execute(f"""
            SELECT
                COALESCE(category, 'uncategorized') AS category,
                COUNT(*) AS cnt
            FROM bf_email be
            WHERE be.active = TRUE AND {date_clause}
            GROUP BY category
            ORDER BY cnt DESC
        """, params)
        return {
            row["category"]: row["cnt"]
            for row in self.env.cr.dictfetchall()
        }

    @api.model
    def _get_response_time(self, date_from=False, date_to=False):
        """Average response time for outbound replies."""
        date_clause, params = self._sql_date_clause(date_from, date_to)
        self.env.cr.execute(f"""
            SELECT
                ROUND(AVG(response_time_hours)::numeric, 1) AS avg_hours,
                ROUND(MIN(response_time_hours)::numeric, 1) AS min_hours,
                ROUND(MAX(response_time_hours)::numeric, 1) AS max_hours,
                COUNT(*) AS sample_count
            FROM bf_email be
            WHERE response_time_hours > 0
              AND be.active = TRUE
              AND {date_clause}
        """, params)
        row = self.env.cr.dictfetchone()
        return {
            "avg_hours": float(row["avg_hours"] or 0),
            "min_hours": float(row["min_hours"] or 0),
            "max_hours": float(row["max_hours"] or 0),
            "sample_count": row["sample_count"] or 0,
        }

    @api.model
    def _get_top_partners(self, date_from=False, date_to=False, limit=10):
        """Top partners by email volume."""
        date_clause, params = self._sql_date_clause(date_from, date_to)
        params.append(limit)
        self.env.cr.execute(f"""
            SELECT
                rp.id AS partner_id,
                rp.name AS partner_name,
                COUNT(*) AS email_count,
                COUNT(*) FILTER (WHERE be.direction = 'in') AS received,
                COUNT(*) FILTER (WHERE be.direction = 'out') AS sent
            FROM bf_email be
            JOIN res_partner rp ON rp.id = be.partner_id
            WHERE be.active = TRUE
              AND {date_clause}
            GROUP BY rp.id, rp.name
            ORDER BY email_count DESC
            LIMIT %s
        """, params)
        return self.env.cr.dictfetchall()

    @api.model
    def _get_daily_volume(self, date_from=False, date_to=False):
        """Daily email volume for the selected range (capped at 366 days).

        When neither bound is provided (preset 'Tout'), derive the range
        from the actual data — ``min(date)`` of bf.email rows to today.
        Falls back to last 14 days only if the table is empty.
        """
        if date_from and date_to:
            start = date_from
            end = date_to
        else:
            end = str(fields.Date.today())
            self.env.cr.execute(
                "SELECT MIN(date)::date FROM bf_email WHERE active = TRUE AND user_id = %s",
                [self.env.uid],
            )
            row = self.env.cr.fetchone()
            min_date = row[0] if row and row[0] else None
            if min_date:
                start = str(min_date)
            else:
                start = str(fields.Date.today() - timedelta(days=14))
        # Cap range to prevent DoS via unbounded generate_series
        max_days = 366
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        if (end_dt - start_dt).days > max_days:
            start = str((end_dt - timedelta(days=max_days)).date())

        self.env.cr.execute("""
            SELECT
                d::date AS day,
                COUNT(*) FILTER (WHERE be.direction = 'in') AS received,
                COUNT(*) FILTER (WHERE be.direction = 'out') AS sent
            FROM generate_series(
                %s::date,
                %s::date,
                '1 day'
            ) AS d
            LEFT JOIN bf_email be
                ON be.date::date = d::date
                AND be.active = TRUE
                AND be.user_id = %s
            GROUP BY d::date
            ORDER BY d::date
        """, [start, end, self.env.uid])
        rows = self.env.cr.dictfetchall()
        return [
            {
                "day": str(r["day"]),
                "received": r["received"] or 0,
                "sent": r["sent"] or 0,
            }
            for r in rows
        ]
