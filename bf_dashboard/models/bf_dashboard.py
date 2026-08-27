import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class BfDashboard(models.AbstractModel):
    _name = "bf.dashboard"
    _description = "Tableau de bord Blue Fox"
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve (BF #24867).

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @api.model
    def get_dashboard_data(self):
        """Return all dashboard sections for the OWL component."""
        return {
            "revenue": self._get_revenue_data(),
            "hosting": self._get_hosting_summary(),
            "knowledge": self._get_knowledge_summary(),
            "privacy": self._get_privacy_summary(),
            "reconciliation": self._get_reconciliation_data(),
            "invoices_to_validate": self._get_invoices_to_validate(),
            "bills_to_pay": self._get_bills_to_pay(),
            "overdue_tasks": self._get_overdue_tasks(),
            "overdue_activities": self._get_overdue_activities(),
        }

    # ------------------------------------------------------------------
    # Private helpers — revenue
    # ------------------------------------------------------------------

    @api.model
    def _get_revenue_data(self):
        """Monthly revenue / direct costs for the last 12 months (raw SQL)."""
        company_id = self.env.company.id
        lang = self.env.user.lang or "en_US"
        self.env.cr.execute(
            """
            SELECT
                TO_CHAR(am.date, 'YYYY-MM') AS month,
                COALESCE(SUM(aml.credit - aml.debit)
                    FILTER (WHERE aa.account_type IN ('income', 'income_other')), 0
                ) AS revenue,
                COALESCE(SUM(aml.debit - aml.credit)
                    FILTER (WHERE aa.account_type = 'expense_direct_cost'), 0
                ) AS costs
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE am.state = 'posted'
              AND am.company_id = %(company_id)s
              AND am.date >= (DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '11 months')::date
              AND aa.account_type IN ('income', 'income_other', 'expense_direct_cost')
            GROUP BY TO_CHAR(am.date, 'YYYY-MM')
            ORDER BY month
            """,
            {"company_id": company_id, "lang": lang},
        )
        rows = self.env.cr.dictfetchall()

        total_revenue = 0.0
        total_costs = 0.0
        months = []
        for r in rows:
            rev = round(float(r["revenue"]), 2)
            cost = round(float(r["costs"]), 2)
            total_revenue += rev
            total_costs += cost
            months.append({
                "month": r["month"],
                "revenue": rev,
                "costs": cost,
                "net": round(rev - cost, 2),
            })

        return {
            "months": months,
            "total_revenue": round(total_revenue, 2),
            "total_costs": round(total_costs, 2),
            "total_net": round(total_revenue - total_costs, 2),
        }

    # ------------------------------------------------------------------
    # Private helpers — module summaries
    # ------------------------------------------------------------------

    @api.model
    def _get_hosting_summary(self):
        """Delegate to hosting.dashboard for key KPIs."""
        try:
            data = self.env["hosting.dashboard"].get_dashboard_data()
            return {
                "services_total": (
                    data["services"]["active"]
                    + data["services"]["suspended"]
                    + data["services"]["expired"]
                ),
                "services_active": data["services"]["active"],
                "services_down": data["uptime_overview"]["down_count"],
                "alerts_count": (
                    data["alerts"]["updates_available"]
                    + data["alerts"]["storage_alerts"]
                    + data["alerts"]["health_issues"]
                ),
                "expiring_count": data["expiring"]["expiring_30"],
            }
        except Exception:
            _logger.exception("Error loading hosting summary")
            return None

    @api.model
    def _get_knowledge_summary(self):
        """Delegate to knowledge.dashboard for key KPIs."""
        try:
            dashboard = self.env["knowledge.dashboard"]
            review = dashboard.get_review_metrics()
            creds = dashboard.get_credential_metrics()
            return {
                "total_attention": review["total_attention"],
                "overdue_review": review["overdue_review"],
                "credentials_expiring": creds["expiring_soon"],
                "credentials_expired": creds["expired"],
            }
        except Exception:
            _logger.exception("Error loading knowledge summary")
            return None

    @api.model
    def _get_privacy_summary(self):
        """Replicate privacy.dashboard KPI logic (avoid TransientModel create)."""
        try:
            now = fields.Datetime.now()
            Consent = self.env["privacy.consent"]

            pending = Consent.search_count([("status", "=", "pending")])
            granted = Consent.search_count([("status", "=", "granted")])
            refused = Consent.search_count([("status", "=", "refused")])

            expiring_30 = Consent.search_count([
                ("status", "=", "granted"),
                ("expires_at", ">", now),
                ("expires_at", "<=", now + timedelta(days=30)),
            ])

            total_responses = granted + refused
            consent_rate = (
                round(granted / total_responses * 100, 1)
                if total_responses > 0
                else 0.0
            )

            return {
                "pending": pending,
                "expiring_30": expiring_30,
                "granted": granted,
                "consent_rate": consent_rate,
            }
        except Exception:
            _logger.exception("Error loading privacy summary")
            return None

    # ------------------------------------------------------------------
    # Private helpers — accounting / operations
    # ------------------------------------------------------------------

    @api.model
    def _get_reconciliation_data(self):
        """Top 10 accounts with unreconciled posted move lines (raw SQL)."""
        company_id = self.env.company.id
        lang = self.env.user.lang or "en_US"
        self.env.cr.execute(
            """
            SELECT
                aa.id AS account_id,
                COALESCE(aa.code_store ->> %(company_id_str)s, '') AS code,
                COALESCE(aa.name ->> %(lang)s, aa.name ->> 'en_US', '') AS name,
                COUNT(*) AS count
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aa.reconcile = TRUE
              AND aml.full_reconcile_id IS NULL
              AND am.state = 'posted'
              AND am.company_id = %(company_id)s
              AND aml.balance != 0
            GROUP BY aa.id, aa.code_store, aa.name
            ORDER BY count DESC
            LIMIT 10
            """,
            {
                "company_id": company_id,
                "company_id_str": str(company_id),
                "lang": lang,
            },
        )
        rows = self.env.cr.dictfetchall()

        total_count = sum(r["count"] for r in rows)
        accounts = [
            {
                "account_id": r["account_id"],
                "code": r["code"],
                "name": r["name"],
                "count": r["count"],
            }
            for r in rows
        ]
        return {
            "accounts": accounts,
            "total_count": total_count,
        }

    @api.model
    def _get_invoices_to_validate(self):
        """Count of draft customer invoices / credit notes."""
        count = self.env["account.move"].search_count([
            ("state", "=", "draft"),
            ("move_type", "in", ("out_invoice", "out_refund")),
        ])
        return {"count": count}

    @api.model
    def _get_bills_to_pay(self):
        """Count and total of unpaid posted vendor bills."""
        self.env.cr.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(amount_residual), 0) AS total
            FROM account_move
            WHERE state = 'posted'
              AND move_type IN ('in_invoice', 'in_refund')
              AND payment_state NOT IN ('paid', 'in_payment', 'reversed')
              AND company_id = %s
            """,
            (self.env.company.id,),
        )
        row = self.env.cr.dictfetchone()
        return {
            "count": row["count"] or 0,
            "total": round(float(row["total"]), 2),
        }

    @api.model
    def _get_overdue_tasks(self):
        """Count of overdue project tasks."""
        today = fields.Date.today()
        count = self.env["project.task"].search_count([
            ("date_deadline", "<=", today),
            ("state", "not in", ("1_done", "1_canceled")),
        ])
        return {"count": count}

    @api.model
    def _get_overdue_activities(self):
        """Count of overdue mail activities."""
        today = fields.Date.today()
        count = self.env["mail.activity"].search_count([
            ("date_deadline", "<=", today),
        ])
        return {"count": count}

    # ------------------------------------------------------------------
    # Navigation actions
    # ------------------------------------------------------------------

    @api.model
    def action_view_draft_invoices(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Factures brouillon",
            "res_model": "account.move",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("state", "=", "draft"),
                ("move_type", "in", ("out_invoice", "out_refund")),
            ],
        }

    @api.model
    def action_view_bills_to_pay(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Factures fournisseurs \u00e0 payer",
            "res_model": "account.move",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("state", "=", "posted"),
                ("move_type", "in", ("in_invoice", "in_refund")),
                ("payment_state", "not in", ("paid", "in_payment", "reversed")),
            ],
        }

    @api.model
    def action_view_overdue_tasks(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "T\u00e2ches en retard",
            "res_model": "project.task",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("date_deadline", "<=", str(today)),
                ("state", "not in", ("1_done", "1_canceled")),
            ],
        }

    @api.model
    def action_view_overdue_activities(self):
        today = fields.Date.today()
        return {
            "type": "ir.actions.act_window",
            "name": "Activit\u00e9s en retard",
            "res_model": "mail.activity",
            "views": [[False, "list"], [False, "form"]],
            "domain": [
                ("date_deadline", "<=", str(today)),
            ],
        }

    @api.model
    def action_view_unreconciled(self, account_id=None):
        domain = [
            ("account_id.reconcile", "=", True),
            ("full_reconcile_id", "=", False),
            ("move_id.state", "=", "posted"),
            ("balance", "!=", 0),
        ]
        if account_id:
            domain.append(("account_id", "=", account_id))
        return {
            "type": "ir.actions.act_window",
            "name": "\u00c9l\u00e9ments \u00e0 lettrer",
            "res_model": "account.move.line",
            "views": [[False, "list"], [False, "form"]],
            "domain": domain,
        }

    @api.model
    def action_open_hosting_dashboard(self):
        return {
            "type": "ir.actions.client",
            "tag": "hosting_dashboard",
            "name": "H\u00e9bergement",
        }

    @api.model
    def action_open_knowledge_dashboard(self):
        return {
            "type": "ir.actions.client",
            "tag": "knowledge_dashboard",
            "name": "Connaissances",
        }

    @api.model
    def action_view_privacy_pending(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Consentements en attente",
            "res_model": "privacy.consent",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("status", "=", "pending")],
        }
